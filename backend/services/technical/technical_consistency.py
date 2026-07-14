"""
TechnicalConsistencyService

Calculates company technical consistency over block-based sub-periods.
Updates existing company_technical_metrics records.
"""
from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import text, update
from sqlalchemy.orm import Session

import config
from models.discovery import CompanyTechnicalMetric

logger = logging.getLogger(__name__)


class TechnicalConsistencyService:
    def __init__(
        self,
        source_session: Session,
        discovery_session: Session,
        benchmark_code: str = None
    ):
        self._src = source_session
        self._disc = discovery_session
        self._benchmark_code = benchmark_code or config.PRIMARY_TECHNICAL_BENCHMARK

    def calculate_and_save_consistency(self, run_id: str, horizon: str) -> None:
        block_cfg = config.CONSISTENCY_BLOCKS.get(horizon)
        if not block_cfg:
            raise ValueError(f"Unknown horizon for consistency: {horizon}")

        num_blocks = block_cfg["num_blocks"]
        sess_per_block = block_cfg["sessions_per_block"]
        total_sessions = num_blocks * sess_per_block

        # 1. Fetch aligned companies
        records = self._disc.execute(
            text("""
                SELECT id, symbol, as_of_date, benchmark_candle_date, calculation_details, warnings
                FROM company_technical_metrics
                WHERE run_id = :r AND horizon = :h
            """),
            {"r": run_id, "h": horizon}
        ).fetchall()

        if not records:
            return

        as_of_date = records[0].as_of_date

        # 2. Fetch benchmark candles <= as_of_date
        bench_rows = self._disc.execute(
            text("""
                SELECT trade_date, close
                FROM benchmark_candles
                WHERE benchmark_code = :code
                  AND trade_date <= :as_of
                ORDER BY trade_date DESC
            """),
            {"code": self._benchmark_code, "as_of": as_of_date}
        ).fetchall()

        bench_dates = [r.trade_date for r in bench_rows]
        bench_closes = [r.close for r in bench_rows]
        bench_idx = {d: i for i, d in enumerate(bench_dates)}

        # 3. Determine required dates for each company
        unique_req_dates = set()
        company_contexts = {}

        for rec in records:
            if not rec.benchmark_candle_date:
                company_contexts[rec.symbol] = {"unavailable_reason": "CONSISTENCY_DATA_UNAVAILABLE", "record": rec}
                continue
                
            bench_date_val = rec.benchmark_candle_date
            if isinstance(bench_date_val, str):
                bench_date_val = date.fromisoformat(bench_date_val)

            b_idx = bench_idx.get(bench_date_val)
            if b_idx is None:
                for bd in bench_dates:
                    if bd <= bench_date_val:
                        b_idx = bench_idx[bd]
                        break
            
            if b_idx is None:
                company_contexts[rec.symbol] = {"unavailable_reason": "CONSISTENCY_DATA_UNAVAILABLE", "record": rec}
                continue

            if b_idx + total_sessions >= len(bench_dates):
                company_contexts[rec.symbol] = {"unavailable_reason": "INSUFFICIENT_BENCHMARK_HISTORY", "record": rec}
                continue

            # Identify the block start/end dates for this company
            blocks = []
            for i in range(num_blocks):
                # i=0 is most recent block
                end_idx = b_idx + i * sess_per_block
                start_idx = b_idx + (i + 1) * sess_per_block
                
                e_date = bench_dates[end_idx]
                s_date = bench_dates[start_idx]
                
                b_end_close = bench_closes[end_idx]
                b_start_close = bench_closes[start_idx]
                
                blocks.append({
                    "start_date": s_date,
                    "end_date": e_date,
                    "b_start_close": b_start_close,
                    "b_end_close": b_end_close
                })
                unique_req_dates.add(s_date)
                unique_req_dates.add(e_date)

            company_contexts[rec.symbol] = {
                "unavailable_reason": None,
                "record": rec,
                "blocks": blocks
            }

        # 4. Fetch company prices EXACTLY on required dates
        syms = list(company_contexts.keys())
        candles_by_sym: dict[str, dict[date, float]] = {}
        
        if unique_req_dates and syms:
            candle_rows = self._src.execute(
                text("""
                    SELECT symbol, DATE(datetime) as dt, close
                    FROM market_candles_cleaned
                    WHERE symbol = ANY(:syms) AND DATE(datetime) = ANY(:req_dates)
                """),
                {"syms": syms, "req_dates": list(unique_req_dates)}
            ).fetchall()

            for r in candle_rows:
                sym = r.symbol.strip()
                if sym not in candles_by_sym:
                    candles_by_sym[sym] = {}
                candles_by_sym[sym][r.dt] = r.close

        # 5. Compute metrics
        values_to_update = []
        for sym, ctx in company_contexts.items():
            rec = ctx.get("record")
            if not rec:
                continue

            existing_warnings = list(rec.warnings) if rec.warnings else []
            calc_details = dict(rec.calculation_details) if rec.calculation_details else {}
            
            update_data = {
                "id": rec.id,
                "positive_period_ratio": None,
                "benchmark_outperformance_ratio": None,
                "company_consistency_score": None,
                "consistency_available": False,
                "warnings": existing_warnings,
                "calculation_details": calc_details
            }

            reason = ctx.get("unavailable_reason")
            if reason:
                if reason not in update_data["warnings"]:
                    update_data["warnings"].append(reason)
                values_to_update.append(update_data)
                continue

            c_candles = candles_by_sym.get(sym, {})
            blocks_def = ctx["blocks"]
            
            valid_blocks = 0
            positive_blocks = 0
            outperf_blocks = 0
            block_results = []
            
            for b in blocks_def:
                s_date = b["start_date"]
                e_date = b["end_date"]
                b_s_close = b["b_start_close"]
                b_e_close = b["b_end_close"]
                
                c_s_close = c_candles.get(s_date)
                c_e_close = c_candles.get(e_date)
                
                # Check validity
                if c_s_close is None or c_e_close is None or c_s_close <= 0 or c_e_close <= 0:
                    block_results.append({
                        "start_date": s_date.isoformat(),
                        "end_date": e_date.isoformat(),
                        "available": False
                    })
                    continue
                    
                if b_s_close is None or b_e_close is None or b_s_close <= 0 or b_e_close <= 0:
                    block_results.append({
                        "start_date": s_date.isoformat(),
                        "end_date": e_date.isoformat(),
                        "available": False
                    })
                    continue
                    
                c_ret = ((c_e_close / c_s_close) - 1.0) * 100.0
                b_ret = ((b_e_close / b_s_close) - 1.0) * 100.0
                
                is_positive = c_ret > 0
                is_outperf = c_ret > b_ret
                
                valid_blocks += 1
                if is_positive:
                    positive_blocks += 1
                if is_outperf:
                    outperf_blocks += 1
                    
                block_results.append({
                    "start_date": s_date.isoformat(),
                    "end_date": e_date.isoformat(),
                    "company_return": c_ret,
                    "benchmark_return": b_ret,
                    "positive": is_positive,
                    "outperformed": is_outperf,
                    "available": True
                })
            
            # Sort blocks by date chronologically
            block_results.sort(key=lambda x: x["start_date"])

            calc_details["consistency"] = {
                "expected_periods": num_blocks,
                "valid_periods": valid_blocks,
                "positive_periods": positive_blocks,
                "outperforming_periods": outperf_blocks,
                "periods": block_results
            }

            if valid_blocks < 2:
                if "INSUFFICIENT_VALID_CONSISTENCY_PERIODS" not in update_data["warnings"]:
                    update_data["warnings"].append("INSUFFICIENT_VALID_CONSISTENCY_PERIODS")
                values_to_update.append(update_data)
                continue

            pos_ratio = (positive_blocks / valid_blocks) * 100.0
            outperf_ratio = (outperf_blocks / valid_blocks) * 100.0
            score = (pos_ratio * 0.5) + (outperf_ratio * 0.5)

            update_data.update({
                "positive_period_ratio": pos_ratio,
                "benchmark_outperformance_ratio": outperf_ratio,
                "company_consistency_score": score,
                "consistency_available": True
            })
            
            values_to_update.append(update_data)

        if not values_to_update:
            return

        # 6. Bulk Update existing records
        self._disc.execute(
            update(CompanyTechnicalMetric),
            values_to_update
        )
        self._disc.commit()

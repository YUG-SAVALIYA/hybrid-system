"""
TechnicalVolumeService

Calculates company volume change over benchmark-aligned windows.
Persists results to company_technical_metrics via UPDATE.
"""
from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import text, update
from sqlalchemy.orm import Session

import config
from models.discovery import CompanyTechnicalMetric

logger = logging.getLogger(__name__)


class TechnicalVolumeService:
    def __init__(
        self,
        source_session: Session,
        discovery_session: Session,
        benchmark_code: str = None
    ):
        self._src = source_session
        self._disc = discovery_session
        self._benchmark_code = benchmark_code or config.PRIMARY_TECHNICAL_BENCHMARK

    def calculate_and_save_volumes(self, run_id: str, horizon: str) -> None:
        h_sessions = {
            "SHORT": config.HORIZON_SHORT_DAYS,
            "MID": config.HORIZON_MID_DAYS,
            "LONG": config.HORIZON_LONG_DAYS
        }.get(horizon)

        if not h_sessions:
            raise ValueError(f"Unknown horizon: {horizon}")

        # 1. Fetch aligned companies from company_technical_metrics
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
                SELECT trade_date
                FROM benchmark_candles
                WHERE benchmark_code = :code
                  AND trade_date <= :as_of
                ORDER BY trade_date DESC
            """),
            {"code": self._benchmark_code, "as_of": as_of_date}
        ).fetchall()

        bench_dates = [r.trade_date for r in bench_rows]
        bench_idx = {d: i for i, d in enumerate(bench_dates)}

        # 3. Determine required date ranges for bulk querying
        min_date = None
        max_date = None
        
        company_contexts = {}
        for rec in records:
            if not rec.benchmark_candle_date:
                company_contexts[rec.symbol] = {"unavailable_reason": "VOLUME_DATA_UNAVAILABLE", "record": rec}
                continue
                
            # If the date is passed as a string from raw DB query
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
                company_contexts[rec.symbol] = {"unavailable_reason": "VOLUME_DATA_UNAVAILABLE", "record": rec}
                continue
                
            # We need exactly 2 * h_sessions ending on b_idx
            if b_idx + 2 * h_sessions > len(bench_dates):
                company_contexts[rec.symbol] = {"unavailable_reason": "INSUFFICIENT_BENCHMARK_HISTORY", "record": rec}
                continue
                
            # Current window: newest date first -> oldest date last
            curr_bench = set(bench_dates[b_idx : b_idx + h_sessions])
            prev_bench = set(bench_dates[b_idx + h_sessions : b_idx + 2 * h_sessions])
            
            oldest_date = bench_dates[b_idx + 2 * h_sessions - 1]
            newest_date = bench_dates[b_idx]
            
            if min_date is None or oldest_date < min_date:
                min_date = oldest_date
            if max_date is None or newest_date > max_date:
                max_date = newest_date
                
            company_contexts[rec.symbol] = {
                "curr_dates": curr_bench,
                "prev_dates": prev_bench,
                "record": rec,
                "unavailable_reason": None,
            }

        # 4. Fetch volumes
        syms = list(company_contexts.keys())
        candles_by_sym: dict[str, dict[date, float]] = {}
        
        if min_date and max_date and syms:
            candle_rows = self._src.execute(
                text("""
                    SELECT symbol, DATE(datetime) as dt, volume
                    FROM market_candles_cleaned
                    WHERE symbol = ANY(:syms)
                      AND DATE(datetime) >= :min_date
                      AND DATE(datetime) <= :max_date
                      AND volume IS NOT NULL
                      AND volume >= 0
                """),
                {"syms": syms, "min_date": min_date, "max_date": max_date}
            ).fetchall()

            for r in candle_rows:
                sym = r.symbol.strip()
                if sym not in candles_by_sym:
                    candles_by_sym[sym] = {}
                candles_by_sym[sym][r.dt] = r.volume
                
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
                "average_volume_current": None,
                "average_volume_previous": None,
                "volume_change": None,
                "volume_available": False,
                "warnings": existing_warnings,
                "calculation_details": calc_details
            }

            reason = ctx.get("unavailable_reason")
            if reason:
                if reason not in update_data["warnings"]:
                    update_data["warnings"].append(reason)
                values_to_update.append(update_data)
                continue
                
            curr_dates = ctx["curr_dates"]
            prev_dates = ctx["prev_dates"]
            
            c_vols = candles_by_sym.get(sym, {})
            
            curr_valid_vols = [c_vols[d] for d in curr_dates if d in c_vols]
            prev_valid_vols = [c_vols[d] for d in prev_dates if d in c_vols]
            
            curr_cov = (len(curr_valid_vols) / h_sessions) * 100.0
            prev_cov = (len(prev_valid_vols) / h_sessions) * 100.0
            
            sorted_curr_dates = sorted(list(curr_dates))
            sorted_prev_dates = sorted(list(prev_dates))
            
            calc_details.update({
                "current_window_start": sorted_curr_dates[0].isoformat() if sorted_curr_dates else None,
                "current_window_end": sorted_curr_dates[-1].isoformat() if sorted_curr_dates else None,
                "previous_window_start": sorted_prev_dates[0].isoformat() if sorted_prev_dates else None,
                "previous_window_end": sorted_prev_dates[-1].isoformat() if sorted_prev_dates else None,
                "expected_sessions_per_window": h_sessions,
                "current_valid_observations": len(curr_valid_vols),
                "previous_valid_observations": len(prev_valid_vols),
                "current_coverage": curr_cov,
                "previous_coverage": prev_cov
            })

            unavailable = False
            if curr_cov < config.MIN_VOLUME_WINDOW_COVERAGE:
                if "INSUFFICIENT_CURRENT_VOLUME_COVERAGE" not in update_data["warnings"]:
                    update_data["warnings"].append("INSUFFICIENT_CURRENT_VOLUME_COVERAGE")
                unavailable = True
            if prev_cov < config.MIN_VOLUME_WINDOW_COVERAGE:
                if "INSUFFICIENT_PREVIOUS_VOLUME_COVERAGE" not in update_data["warnings"]:
                    update_data["warnings"].append("INSUFFICIENT_PREVIOUS_VOLUME_COVERAGE")
                unavailable = True

            if unavailable:
                values_to_update.append(update_data)
                continue

            # calculate averages
            curr_avg = sum(curr_valid_vols) / len(curr_valid_vols)
            prev_avg = sum(prev_valid_vols) / len(prev_valid_vols)
            
            if prev_avg <= 0:
                if "ZERO_PREVIOUS_AVERAGE_VOLUME" not in update_data["warnings"]:
                    update_data["warnings"].append("ZERO_PREVIOUS_AVERAGE_VOLUME")
                values_to_update.append(update_data)
                continue
                
            vol_change = ((curr_avg / prev_avg) - 1.0) * 100.0
            
            update_data.update({
                "average_volume_current": curr_avg,
                "average_volume_previous": prev_avg,
                "volume_change": vol_change,
                "volume_available": True
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

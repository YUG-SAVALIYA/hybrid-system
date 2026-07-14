"""
TechnicalReturnService

Calculates company return, benchmark return, and relative return.
Persists results to company_technical_metrics.
"""
from __future__ import annotations

import uuid
import logging
from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert

import config
from models.discovery import CompanyTechnicalMetric
from services.technical.technical_date_alignment import AlignmentResult

logger = logging.getLogger(__name__)


class TechnicalReturnService:
    """
    Calculates returns based on an aligned date window.
    """

    def __init__(
        self,
        source_session: Session,
        discovery_session: Session,
        benchmark_code: str = None
    ):
        self._src = source_session
        self._disc = discovery_session
        self._benchmark_code = benchmark_code or config.PRIMARY_TECHNICAL_BENCHMARK

    def calculate_and_save_returns(self, run_id: str, alignment: AlignmentResult) -> None:
        if alignment.status != "READY" or not alignment.companies:
            return

        horizon = alignment.horizon
        h_sessions = {
            "SHORT": config.HORIZON_SHORT_DAYS,
            "MID": config.HORIZON_MID_DAYS,
            "LONG": config.HORIZON_LONG_DAYS
        }.get(horizon)

        if not h_sessions:
            raise ValueError(f"Unknown horizon: {horizon}")

        # 1. Fetch benchmark candles <= as_of_date (ordered newest to oldest)
        bench_rows = self._disc.execute(
            text("""
                SELECT trade_date, close
                FROM benchmark_candles
                WHERE benchmark_code = :code
                  AND trade_date <= :as_of
                ORDER BY trade_date DESC
            """),
            {"code": self._benchmark_code, "as_of": alignment.as_of_date}
        ).fetchall()

        bench_dates = [r.trade_date for r in bench_rows]
        bench_closes = [r.close for r in bench_rows]
        bench_idx = {d: i for i, d in enumerate(bench_dates)}

        # 2. Fetch company hierarchy info
        syms = [c.symbol for c in alignment.companies if c.symbol]
        if not syms:
            return

        company_info_rows = self._src.execute(
            text("""
                SELECT share_symbol, sectore, industry, categorized_industry
                FROM companies
                WHERE share_symbol = ANY(:syms)
            """),
            {"syms": syms}
        ).fetchall()
        
        info_by_sym = {
            r.share_symbol.strip(): {
                "sector": r.sectore or "",
                "industry": r.industry or "",
                "basic_industry": r.categorized_industry or ""
            }
            for r in company_info_rows if r.share_symbol
        }

        # 3. First pass: determine exact required start and end dates for each company
        company_required_dates = {}
        unique_req_dates = set()
        
        for comp in alignment.companies:
            if not comp.available or not comp.company_candle_date:
                continue
                
            b_idx = bench_idx.get(comp.company_candle_date)
            if b_idx is None:
                # Closest earlier session
                for bd in bench_dates:
                    if bd <= comp.company_candle_date:
                        b_idx = bench_idx[bd]
                        break
                        
            if b_idx is None or b_idx + h_sessions >= len(bench_dates):
                continue
                
            req_end = bench_dates[b_idx]
            req_start = bench_dates[b_idx + h_sessions]
            
            company_required_dates[comp.symbol] = {
                "end": req_end,
                "start": req_start,
                "b_idx": b_idx
            }
            unique_req_dates.add(req_end)
            unique_req_dates.add(req_start)

        # 4. Fetch company prices exactly on the required dates
        candles_by_sym: dict[str, dict[date, float]] = {}
        if unique_req_dates:
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
        values_to_insert = []
        for comp in alignment.companies:
            sym = comp.symbol
            info = info_by_sym.get(sym, {})
            
            record = {
                "id": str(uuid.uuid4()),
                "run_id": run_id,
                "source_company_id": comp.source_company_id,
                "symbol": sym,
                "sector": info.get("sector", ""),
                "industry": info.get("industry", ""),
                "basic_industry": info.get("basic_industry", ""),
                "horizon": horizon,
                "as_of_date": alignment.as_of_date,
                "company_candle_date": comp.company_candle_date,
                "benchmark_candle_date": comp.company_candle_date,  # default placeholder
                "current_close": None,
                "start_close": None,
                "company_return": None,
                "benchmark_current_close": None,
                "benchmark_start_close": None,
                "benchmark_return": None,
                "relative_return": None,
                "return_available": False,
                "warnings": list(comp.warnings),
                "calculation_details": {}
            }

            if not comp.available:
                values_to_insert.append(record)
                continue

            if not comp.company_candle_date:
                record["warnings"].append("NO_COMPANY_CANDLE_DATE")
                values_to_insert.append(record)
                continue

            reqs = company_required_dates.get(sym)
            if not reqs:
                # Happens if benchmark data was insufficient or unaligned
                record["warnings"].append("INSUFFICIENT_ALIGNED_BENCHMARK_HISTORY")
                values_to_insert.append(record)
                continue
                
            req_end = reqs["end"]
            req_start = reqs["start"]
            b_idx = reqs["b_idx"]
            
            record["benchmark_candle_date"] = req_end

            c_candles = candles_by_sym.get(sym, {})
            curr_close = c_candles.get(req_end)
            start_close = c_candles.get(req_start)

            if curr_close is None and start_close is None:
                record["warnings"].append("EXACT_COMPANY_CANDLES_MISSING")
                values_to_insert.append(record)
                continue
            elif curr_close is None:
                record["warnings"].append("MISSING_COMPANY_END_DATE_CANDLE")
                values_to_insert.append(record)
                continue
            elif start_close is None:
                record["warnings"].append("MISSING_COMPANY_START_DATE_CANDLE")
                values_to_insert.append(record)
                continue
                
            if curr_close <= 0 or start_close <= 0:
                record["warnings"].append("INVALID_COMPANY_CLOSE")
                values_to_insert.append(record)
                continue
                
            b_curr_close = bench_closes[b_idx]
            b_start_close = bench_closes[b_idx + h_sessions]
            
            if b_curr_close is None or b_curr_close <= 0 or b_start_close is None or b_start_close <= 0:
                record["warnings"].append("INVALID_BENCHMARK_CLOSE")
                values_to_insert.append(record)
                continue
                
            # Compute returns strictly over identical dates
            c_ret = ((curr_close / start_close) - 1.0) * 100.0
            b_ret = ((b_curr_close / b_start_close) - 1.0) * 100.0
            rel_ret = c_ret - b_ret
            
            record.update({
                "current_close": curr_close,
                "start_close": start_close,
                "company_return": c_ret,
                "benchmark_current_close": b_curr_close,
                "benchmark_start_close": b_start_close,
                "benchmark_return": b_ret,
                "relative_return": rel_ret,
                "return_available": True
            })
            
            values_to_insert.append(record)

        if not values_to_insert:
            return

        # 6. Upsert to DB

        stmt = insert(CompanyTechnicalMetric).values(values_to_insert)
        
        # Fields to update on conflict
        update_cols = [
            'sector', 'industry', 'basic_industry',
            'as_of_date', 'company_candle_date', 'benchmark_candle_date',
            'current_close', 'start_close', 'company_return',
            'benchmark_current_close', 'benchmark_start_close', 'benchmark_return',
            'relative_return', 'return_available', 'warnings', 'calculation_details'
        ]
        
        update_dict = {
            c.name: c for c in stmt.excluded if c.name in update_cols
        }

        stmt = stmt.on_conflict_do_update(
            constraint='uq_technical_run_company_horizon',
            set_=update_dict
        )
        
        self._disc.execute(stmt)
        self._disc.commit()

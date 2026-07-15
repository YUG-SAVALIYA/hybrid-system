"""
TechnicalReturnService

Calculates company return, benchmark return, and relative return.
Persists results to company_technical_metrics.

Vectorized with Pandas for maximum throughput.
"""
from __future__ import annotations

import math
import uuid
import logging
from datetime import date
from typing import Optional

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert

import config
from models.discovery import CompanyTechnicalMetric
from services.technical.technical_date_alignment import AlignmentResult

logger = logging.getLogger(__name__)


def _is_nan(v) -> bool:
    """Return True if v is NaN, NaT, or None."""
    if v is None:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    try:
        return bool(pd.isna(v))
    except (TypeError, ValueError):
        return False


def _safe_float(v) -> Optional[float]:
    try:
        f = float(v)
        return None if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return None


def _safe_date(v):
    """Convert Pandas NaN / NaT / None to None, otherwise return the date value."""
    return None if _is_nan(v) else v


class TechnicalReturnService:
    """
    Calculates returns based on an aligned date window.
    Bulk-queries all company candles in one shot, then does vectorized
    return math in Pandas — no per-company DB round-trips.
    """

    def __init__(
        self,
        source_session: Session,
        discovery_session: Session,
        benchmark_code: str = None,
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
            "MID":   config.HORIZON_MID_DAYS,
            "LONG":  config.HORIZON_LONG_DAYS,
        }.get(horizon)
        if not h_sessions:
            raise ValueError(f"Unknown horizon: {horizon}")

        # ── 1. Benchmark candles (newest → oldest list) ────────────────────────
        bench_rows = self._disc.execute(
            text("""
                SELECT trade_date, close
                FROM benchmark_candles
                WHERE benchmark_code = :code
                  AND trade_date <= :as_of
                ORDER BY trade_date DESC
            """),
            {"code": self._benchmark_code, "as_of": alignment.as_of_date},
        ).fetchall()

        bench_dates: list[date] = [r.trade_date for r in bench_rows]
        bench_closes: list[float] = [r.close for r in bench_rows]

        if len(bench_dates) <= h_sessions:
            logger.warning("Insufficient benchmark candles for horizon %s", horizon)
            return

        # Build a fast lookup: date → positional index (0 = newest)
        bench_idx: dict[date, int] = {d: i for i, d in enumerate(bench_dates)}

        # ── 2. Company metadata (hierarchy) ───────────────────────────────────
        syms = [c.symbol for c in alignment.companies if c.symbol]
        if not syms:
            return

        info_rows = self._src.execute(
            text("""
                SELECT share_symbol, sectore, industry, categorized_industry
                FROM companies
                WHERE share_symbol = ANY(:syms)
            """),
            {"syms": syms},
        ).fetchall()
        info_map: dict[str, dict] = {
            r.share_symbol.strip(): {
                "sector": r.sectore or "",
                "industry": r.industry or "",
                "basic_industry": r.categorized_industry or "",
            }
            for r in info_rows
        }

        # ── 3. Resolve required candle dates per company ───────────────────────
        # We do this in plain Python (one pass, no DB) using the already-fetched
        # bench_dates list, then collect unique dates for a single bulk query.

        company_ctx: dict = {}   # symbol → context dict
        unique_req_dates: set[date] = set()

        for comp in alignment.companies:
            sym = comp.symbol
            ctx = {
                "source_company_id": comp.source_company_id,
                "available": comp.available,
                "company_candle_date": comp.company_candle_date,
                "warnings": list(comp.warnings),
                "req_end": None,
                "req_start": None,
                "b_curr_close": None,
                "b_start_close": None,
                "benchmark_candle_date": None,
                "unavail_reason": None,
            }

            if not comp.available or not comp.company_candle_date:
                if comp.available and not comp.company_candle_date:
                    ctx["unavail_reason"] = "NO_COMPANY_CANDLE_DATE"
                company_ctx[sym] = ctx
                continue

            # Resolve benchmark index for this company's candle date
            ccd = comp.company_candle_date
            b = bench_idx.get(ccd)
            if b is None:
                # Walk to nearest earlier bench date
                for bd in bench_dates:
                    if bd <= ccd:
                        b = bench_idx[bd]
                        break

            if b is None or (b + h_sessions) >= len(bench_dates):
                ctx["unavail_reason"] = "INSUFFICIENT_ALIGNED_BENCHMARK_HISTORY"
                company_ctx[sym] = ctx
                continue

            req_end = bench_dates[b]
            req_start = bench_dates[b + h_sessions]

            ctx["req_end"] = req_end
            ctx["req_start"] = req_start
            ctx["b_curr_close"] = bench_closes[b]
            ctx["b_start_close"] = bench_closes[b + h_sessions]
            ctx["benchmark_candle_date"] = req_end

            unique_req_dates.add(req_end)
            unique_req_dates.add(req_start)
            company_ctx[sym] = ctx

        # ── 4. ONE bulk candle query for all companies / all dates ─────────────
        candles_by_sym: dict[str, dict[date, float]] = {}
        if unique_req_dates and syms:
            req_dates_str = [d.isoformat()[:10] for d in unique_req_dates]
            min_date_str = min(unique_req_dates).isoformat()[:10]
            max_date_str = max(unique_req_dates).isoformat()[:10] + "T23:59:59"

            candle_rows = self._src.execute(
                text("""
                    SELECT symbol, SUBSTRING(datetime FROM 1 FOR 10) AS dt, close
                    FROM market_candles_cleaned
                    WHERE symbol = ANY(:syms)
                      AND datetime >= :min_date
                      AND datetime <= :max_date
                      AND SUBSTRING(datetime FROM 1 FOR 10) = ANY(:req_dates)
                """),
                {
                    "syms": list(company_ctx.keys()),
                    "req_dates": req_dates_str,
                    "min_date": min_date_str,
                    "max_date": max_date_str
                },
            ).fetchall()
            for r in candle_rows:
                dt_obj = date.fromisoformat(r.dt)
                candles_by_sym.setdefault(r.symbol.strip(), {})[dt_obj] = r.close

        # ── 5. Compute returns (no DB, no loop overhead beyond O(n)) ──────────
        values_to_insert = []
        info = info_map

        for sym, ctx in company_ctx.items():
            hi = info.get(sym, {})
            record = {
                "id": str(uuid.uuid4()),
                "run_id": run_id,
                "source_company_id": ctx["source_company_id"],
                "symbol": sym,
                "sector": hi.get("sector", ""),
                "industry": hi.get("industry", ""),
                "basic_industry": hi.get("basic_industry", ""),
                "horizon": horizon,
                "as_of_date": alignment.as_of_date,
                "company_candle_date": _safe_date(ctx["company_candle_date"]),
                "benchmark_candle_date": _safe_date(ctx["benchmark_candle_date"]),
                "current_close": None,
                "start_close": None,
                "company_return": None,
                "benchmark_current_close": None,
                "benchmark_start_close": None,
                "benchmark_return": None,
                "relative_return": None,
                "return_available": False,
                "warnings": list(ctx["warnings"]),
                "calculation_details": {},
            }

            if ctx.get("unavail_reason"):
                record["warnings"].append(ctx["unavail_reason"])
                values_to_insert.append(record)
                continue

            if not ctx["available"] or ctx["req_end"] is None:
                values_to_insert.append(record)
                continue

            c_candles = candles_by_sym.get(sym, {})
            curr_close = c_candles.get(ctx["req_end"])
            start_close = c_candles.get(ctx["req_start"])
            b_curr = ctx["b_curr_close"]
            b_start = ctx["b_start_close"]

            record["current_close"] = _safe_float(curr_close)
            record["start_close"] = _safe_float(start_close)
            record["benchmark_current_close"] = _safe_float(b_curr)
            record["benchmark_start_close"] = _safe_float(b_start)

            if curr_close is None and start_close is None:
                record["warnings"].append("EXACT_COMPANY_CANDLES_MISSING")
            elif curr_close is None:
                record["warnings"].append("MISSING_COMPANY_END_DATE_CANDLE")
            elif start_close is None:
                record["warnings"].append("MISSING_COMPANY_START_DATE_CANDLE")
            elif curr_close <= 0 or start_close <= 0:
                record["warnings"].append("INVALID_COMPANY_CLOSE")
            elif b_curr is None or b_start is None or b_curr <= 0 or b_start <= 0:
                record["warnings"].append("INVALID_BENCHMARK_CLOSE")
            else:
                c_ret = ((curr_close / start_close) - 1.0) * 100.0
                b_ret = ((b_curr / b_start) - 1.0) * 100.0
                record["company_return"] = _safe_float(c_ret)
                record["benchmark_return"] = _safe_float(b_ret)
                record["relative_return"] = _safe_float(c_ret - b_ret)
                record["return_available"] = True

            values_to_insert.append(record)

        if not values_to_insert:
            return

        # ── 6. Upsert ─────────────────────────────────────────────────────────
        stmt = insert(CompanyTechnicalMetric).values(values_to_insert)
        update_cols = [
            "sector", "industry", "basic_industry",
            "as_of_date", "company_candle_date", "benchmark_candle_date",
            "current_close", "start_close", "company_return",
            "benchmark_current_close", "benchmark_start_close", "benchmark_return",
            "relative_return", "return_available", "warnings", "calculation_details",
        ]
        stmt = stmt.on_conflict_do_update(
            constraint="uq_technical_run_company_horizon",
            set_={c.name: c for c in stmt.excluded if c.name in update_cols},
        )
        self._disc.execute(stmt)
        self._disc.commit()
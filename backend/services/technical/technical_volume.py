"""
TechnicalVolumeService

Calculates company volume change over benchmark-aligned windows.
Persists results to company_technical_metrics via UPDATE.

Key optimisation: SQL GROUP BY aggregation (SUM/COUNT per symbol) avoids
transferring raw per-day volume rows to Python. Instead of N*sessions rows,
only 1 aggregated row per symbol crosses the wire.
"""
from __future__ import annotations

import math
import logging
from collections import defaultdict
from datetime import date
from typing import Optional

from sqlalchemy import text, update
from sqlalchemy.orm import Session

import config
from models.discovery import CompanyTechnicalMetric

logger = logging.getLogger(__name__)


def _is_nan(v) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    try:
        import pandas as pd
        return bool(pd.isna(v))
    except (TypeError, ValueError, ImportError):
        return False


class TechnicalVolumeService:
    def __init__(
        self,
        source_session: Session,
        discovery_session: Session,
        benchmark_code: str = None,
    ):
        self._src = source_session
        self._disc = discovery_session
        self._benchmark_code = benchmark_code or config.PRIMARY_TECHNICAL_BENCHMARK

    def calculate_and_save_volumes(self, run_id: str, horizon: str) -> None:
        h_sessions = {
            "SHORT": config.HORIZON_SHORT_DAYS,
            "MID":   config.HORIZON_MID_DAYS,
            "LONG":  config.HORIZON_LONG_DAYS,
        }.get(horizon)
        if not h_sessions:
            raise ValueError(f"Unknown horizon: {horizon}")

        # ── 1. Load existing metric records ───────────────────────────────────
        records = self._disc.execute(
            text("""
                SELECT id, symbol, as_of_date, benchmark_candle_date,
                       calculation_details, warnings
                FROM company_technical_metrics
                WHERE run_id = :r AND horizon = :h
            """),
            {"r": run_id, "h": horizon},
        ).fetchall()

        if not records:
            return

        as_of_date = records[0].as_of_date

        # ── 2. Benchmark dates (newest → oldest) ──────────────────────────────
        bench_rows = self._disc.execute(
            text("""
                SELECT trade_date
                FROM benchmark_candles
                WHERE benchmark_code = :code AND trade_date <= :as_of
                ORDER BY trade_date DESC
            """),
            {"code": self._benchmark_code, "as_of": as_of_date},
        ).fetchall()
        bench_dates: list[date] = [r.trade_date for r in bench_rows]
        bench_idx: dict[date, int] = {d: i for i, d in enumerate(bench_dates)}

        # ── 3. Resolve window date-sets per company ────────────────────────────
        company_ctxs: dict = {}   # rec_id → context dict

        for rec in records:
            sym = rec.symbol
            ctx = {
                "id": rec.id,
                "symbol": sym,
                "existing_warnings": list(rec.warnings or []),
                "calc_details": dict(rec.calculation_details or {}),
                "unavail_reason": None,
                "curr_set": None,
                "prev_set": None,
            }

            bd = rec.benchmark_candle_date
            if not bd or _is_nan(bd):
                ctx["unavail_reason"] = "VOLUME_DATA_UNAVAILABLE"
                company_ctxs[rec.id] = ctx
                continue

            if isinstance(bd, str):
                bd = date.fromisoformat(bd)

            b = bench_idx.get(bd)
            if b is None:
                for d in bench_dates:
                    if d <= bd:
                        b = bench_idx[d]
                        break

            if b is None:
                ctx["unavail_reason"] = "VOLUME_DATA_UNAVAILABLE"
                company_ctxs[rec.id] = ctx
                continue

            if b + 2 * h_sessions > len(bench_dates):
                ctx["unavail_reason"] = "INSUFFICIENT_BENCHMARK_HISTORY"
                company_ctxs[rec.id] = ctx
                continue

            ctx["curr_set"] = frozenset(bench_dates[b: b + h_sessions])
            ctx["prev_set"] = frozenset(bench_dates[b + h_sessions: b + 2 * h_sessions])
            company_ctxs[rec.id] = ctx

        # ── 4. Group companies by (curr_set, prev_set) ────────────────────────
        # Companies sharing the same benchmark date share the same window.
        # In practice this is usually just 1-3 groups.
        # For each group we fire ONE aggregating SQL query that returns a single
        # SUM/COUNT row per symbol — no raw daily rows transferred.
        window_groups: dict = defaultdict(list)  # (curr_set, prev_set) → [ctx]
        for ctx in company_ctxs.values():
            if ctx["unavail_reason"] is None:
                key = (ctx["curr_set"], ctx["prev_set"])
                window_groups[key].append(ctx)

        # agg_map[symbol] = {curr_sum, curr_count, prev_sum, prev_count}
        agg_map: dict[str, dict] = {}

        for (curr_set, prev_set), ctxs in window_groups.items():
            group_syms = [c["symbol"] for c in ctxs]
            curr_dates  = list(curr_set)
            prev_dates  = list(prev_set)
            all_dates   = list(curr_set | prev_set)

            curr_dates_str = [d.isoformat()[:10] for d in curr_dates]
            prev_dates_str = [d.isoformat()[:10] for d in prev_dates]
            min_date_str = min(all_dates).isoformat()[:10]
            max_date_str = max(all_dates).isoformat()[:10] + "T23:59:59"

            # One SQL query per window group — pushes AVG/COUNT to PostgreSQL
            agg_rows = self._src.execute(
                text("""
                    SELECT
                        symbol,
                        SUM(CASE WHEN SUBSTRING(datetime FROM 1 FOR 10) = ANY(:curr_dates)
                                 THEN volume ELSE 0 END)                         AS curr_sum,
                        COUNT(CASE WHEN SUBSTRING(datetime FROM 1 FOR 10) = ANY(:curr_dates)
                                    AND volume IS NOT NULL AND volume >= 0
                                   THEN 1 END)                                   AS curr_count,
                        SUM(CASE WHEN SUBSTRING(datetime FROM 1 FOR 10) = ANY(:prev_dates)
                                 THEN volume ELSE 0 END)                         AS prev_sum,
                        COUNT(CASE WHEN SUBSTRING(datetime FROM 1 FOR 10) = ANY(:prev_dates)
                                    AND volume IS NOT NULL AND volume >= 0
                                   THEN 1 END)                                   AS prev_count
                    FROM market_candles_cleaned
                    WHERE symbol = ANY(:syms)
                      AND datetime >= :min_date
                      AND datetime <= :max_date
                    GROUP BY symbol
                """),
                {
                    "syms":       group_syms,
                    "curr_dates": curr_dates_str,
                    "prev_dates": prev_dates_str,
                    "min_date":   min_date_str,
                    "max_date":   max_date_str,
                },
            ).fetchall()

            for r in agg_rows:
                agg_map[r.symbol.strip()] = {
                    "curr_sum":   float(r.curr_sum  or 0),
                    "curr_count": int(r.curr_count  or 0),
                    "prev_sum":   float(r.prev_sum  or 0),
                    "prev_count": int(r.prev_count  or 0),
                }

        # ── 5. Compute coverage & averages from aggregated data ────────────────
        values_to_update = []

        for ctx in company_ctxs.values():
            existing_warnings = list(ctx["existing_warnings"])
            calc_details = dict(ctx["calc_details"])

            upd = {
                "id": ctx["id"],
                "average_volume_current": None,
                "average_volume_previous": None,
                "volume_change": None,
                "volume_available": False,
                "warnings": existing_warnings,
                "calculation_details": calc_details,
            }

            if ctx["unavail_reason"]:
                if ctx["unavail_reason"] not in upd["warnings"]:
                    upd["warnings"].append(ctx["unavail_reason"])
                values_to_update.append(upd)
                continue

            agg = agg_map.get(ctx["symbol"], {})
            curr_count = agg.get("curr_count", 0)
            prev_count = agg.get("prev_count", 0)
            curr_sum   = agg.get("curr_sum",   0.0)
            prev_sum   = agg.get("prev_sum",   0.0)

            curr_cov = (curr_count / h_sessions) * 100.0
            prev_cov = (prev_count / h_sessions) * 100.0

            sorted_curr = sorted(ctx["curr_set"])
            sorted_prev = sorted(ctx["prev_set"])
            calc_details.update({
                "current_window_start":  sorted_curr[0].isoformat() if sorted_curr else None,
                "current_window_end":    sorted_curr[-1].isoformat() if sorted_curr else None,
                "previous_window_start": sorted_prev[0].isoformat() if sorted_prev else None,
                "previous_window_end":   sorted_prev[-1].isoformat() if sorted_prev else None,
                "expected_sessions_per_window": h_sessions,
                "current_valid_observations":   curr_count,
                "previous_valid_observations":  prev_count,
                "current_coverage":  curr_cov,
                "previous_coverage": prev_cov,
            })

            unavailable = False
            if curr_cov < config.MIN_VOLUME_WINDOW_COVERAGE:
                if "INSUFFICIENT_CURRENT_VOLUME_COVERAGE" not in upd["warnings"]:
                    upd["warnings"].append("INSUFFICIENT_CURRENT_VOLUME_COVERAGE")
                unavailable = True
            if prev_cov < config.MIN_VOLUME_WINDOW_COVERAGE:
                if "INSUFFICIENT_PREVIOUS_VOLUME_COVERAGE" not in upd["warnings"]:
                    upd["warnings"].append("INSUFFICIENT_PREVIOUS_VOLUME_COVERAGE")
                unavailable = True

            if unavailable:
                values_to_update.append(upd)
                continue

            if curr_count == 0:
                if "INSUFFICIENT_CURRENT_VOLUME_COVERAGE" not in upd["warnings"]:
                    upd["warnings"].append("INSUFFICIENT_CURRENT_VOLUME_COVERAGE")
                values_to_update.append(upd)
                continue

            curr_avg = curr_sum / curr_count
            prev_avg = prev_sum / prev_count if prev_count > 0 else 0.0

            if prev_avg <= 0:
                if "ZERO_PREVIOUS_AVERAGE_VOLUME" not in upd["warnings"]:
                    upd["warnings"].append("ZERO_PREVIOUS_AVERAGE_VOLUME")
                values_to_update.append(upd)
                continue

            upd.update({
                "average_volume_current":  curr_avg,
                "average_volume_previous": prev_avg,
                "volume_change": ((curr_avg / prev_avg) - 1.0) * 100.0,
                "volume_available": True,
                "calculation_details": calc_details,
            })
            values_to_update.append(upd)

        if not values_to_update:
            return

        self._disc.execute(update(CompanyTechnicalMetric), values_to_update)
        self._disc.commit()

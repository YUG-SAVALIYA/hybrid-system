"""
TechnicalConsistencyService

Calculates company technical consistency over block-based sub-periods.
Updates existing company_technical_metrics records.

Key optimisation: ONE bulk candle query for all companies + all block dates
(only 2 dates per block × num_blocks — very small dataset).
Companies sharing the same benchmark date share identical block dates,
so one SQL query covers the entire group.
"""
from __future__ import annotations

import math
import logging
from collections import defaultdict
from datetime import date

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


class TechnicalConsistencyService:
    def __init__(
        self,
        source_session: Session,
        discovery_session: Session,
        benchmark_code: str = None,
    ):
        self._src = source_session
        self._disc = discovery_session
        self._benchmark_code = benchmark_code or config.PRIMARY_TECHNICAL_BENCHMARK

    def calculate_and_save_consistency(self, run_id: str, horizon: str) -> None:
        block_cfg = config.CONSISTENCY_BLOCKS.get(horizon)
        if not block_cfg:
            raise ValueError(f"Unknown horizon for consistency: {horizon}")

        num_blocks    = block_cfg["num_blocks"]
        sess_per_block = block_cfg["sessions_per_block"]
        total_sessions = num_blocks * sess_per_block

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

        # ── 2. Benchmark candles (newest → oldest) ────────────────────────────
        bench_rows = self._disc.execute(
            text("""
                SELECT trade_date, close
                FROM benchmark_candles
                WHERE benchmark_code = :code AND trade_date <= :as_of
                ORDER BY trade_date DESC
            """),
            {"code": self._benchmark_code, "as_of": as_of_date},
        ).fetchall()
        bench_dates: list[date] = [r.trade_date for r in bench_rows]
        bench_closes: list[float] = [r.close for r in bench_rows]
        bench_idx: dict[date, int] = {d: i for i, d in enumerate(bench_dates)}

        # ── 3. Resolve block definitions per company ───────────────────────────
        company_ctxs: dict = {}
        # Group by (benchmark_candle_date) → same block structure → one SQL query
        bd_groups: dict[date, list] = defaultdict(list)  # bd → [ctx]

        for rec in records:
            sym = rec.symbol
            ctx = {
                "id": rec.id,
                "symbol": sym,
                "existing_warnings": list(rec.warnings or []),
                "calc_details": dict(rec.calculation_details or {}),
                "unavail_reason": None,
                "blocks": None,
                "bench_candle_date": None,
            }

            bd = rec.benchmark_candle_date
            if not bd or _is_nan(bd):
                ctx["unavail_reason"] = "CONSISTENCY_DATA_UNAVAILABLE"
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
                ctx["unavail_reason"] = "CONSISTENCY_DATA_UNAVAILABLE"
                company_ctxs[rec.id] = ctx
                continue

            if b + total_sessions >= len(bench_dates):
                ctx["unavail_reason"] = "INSUFFICIENT_BENCHMARK_HISTORY"
                company_ctxs[rec.id] = ctx
                continue

            blocks = []
            for i in range(num_blocks):
                end_idx   = b + i * sess_per_block
                start_idx = b + (i + 1) * sess_per_block
                blocks.append({
                    "start_date":   bench_dates[start_idx],
                    "end_date":     bench_dates[end_idx],
                    "b_start_close": bench_closes[start_idx],
                    "b_end_close":   bench_closes[end_idx],
                })

            ctx["blocks"] = blocks
            ctx["bench_candle_date"] = bd
            company_ctxs[rec.id] = ctx
            bd_groups[bd].append(ctx)

        # ── 4. ONE bulk candle query per benchmark-date group ─────────────────
        # Each group has the same block start/end dates, so they share the
        # required_dates set. We fetch all company candles for those dates in
        # one shot and build a symbol → {date: close} lookup.
        # The dataset is tiny: num_blocks * 2 dates × N symbols.
        candle_lookup: dict[str, dict[date, float]] = {}

        for bd, ctxs_in_group in bd_groups.items():
            # Block dates are identical for all companies in this group
            unique_req_dates: set[date] = set()
            for b in ctxs_in_group[0]["blocks"]:
                unique_req_dates.add(b["start_date"])
                unique_req_dates.add(b["end_date"])

            group_syms = [c["symbol"] for c in ctxs_in_group]

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
                    "syms": group_syms,
                    "req_dates": req_dates_str,
                    "min_date": min_date_str,
                    "max_date": max_date_str
                },
            ).fetchall()
            
            for r in candle_rows:
                dt_obj = date.fromisoformat(r.dt)
                candle_lookup.setdefault(r.symbol.strip(), {})[dt_obj] = r.close

        # ── 5. Compute consistency metrics ────────────────────────────────────
        values_to_update = []

        for ctx in company_ctxs.values():
            existing_warnings = list(ctx["existing_warnings"])
            calc_details = dict(ctx["calc_details"])

            upd = {
                "id": ctx["id"],
                "positive_period_ratio": None,
                "benchmark_outperformance_ratio": None,
                "company_consistency_score": None,
                "consistency_available": False,
                "warnings": existing_warnings,
                "calculation_details": calc_details,
            }

            if ctx["unavail_reason"]:
                if ctx["unavail_reason"] not in upd["warnings"]:
                    upd["warnings"].append(ctx["unavail_reason"])
                values_to_update.append(upd)
                continue

            c_candles = candle_lookup.get(ctx["symbol"], {})
            valid_blocks   = 0
            positive_blocks = 0
            outperf_blocks  = 0
            block_results  = []

            for b in ctx["blocks"]:
                s_date = b["start_date"]
                e_date = b["end_date"]
                b_s    = b["b_start_close"]
                b_e    = b["b_end_close"]

                c_s = c_candles.get(s_date)
                c_e = c_candles.get(e_date)

                if not (c_s and c_e and c_s > 0 and c_e > 0
                        and b_s and b_e and b_s > 0 and b_e > 0):
                    block_results.append({
                        "start_date": s_date.isoformat(),
                        "end_date":   e_date.isoformat(),
                        "available":  False,
                    })
                    continue

                c_ret = ((c_e / c_s) - 1.0) * 100.0
                b_ret = ((b_e / b_s) - 1.0) * 100.0
                is_positive = c_ret > 0
                is_outperf  = c_ret > b_ret

                valid_blocks += 1
                if is_positive:  positive_blocks += 1
                if is_outperf:   outperf_blocks  += 1

                block_results.append({
                    "start_date":      s_date.isoformat(),
                    "end_date":        e_date.isoformat(),
                    "company_return":  c_ret,
                    "benchmark_return": b_ret,
                    "positive":        is_positive,
                    "outperformed":    is_outperf,
                    "available":       True,
                })

            block_results.sort(key=lambda x: x["start_date"])
            calc_details["consistency"] = {
                "expected_periods":     num_blocks,
                "valid_periods":        valid_blocks,
                "positive_periods":     positive_blocks,
                "outperforming_periods": outperf_blocks,
                "periods":              block_results,
            }

            if valid_blocks < 2:
                if "INSUFFICIENT_VALID_CONSISTENCY_PERIODS" not in upd["warnings"]:
                    upd["warnings"].append("INSUFFICIENT_VALID_CONSISTENCY_PERIODS")
                values_to_update.append(upd)
                continue

            pos_ratio     = (positive_blocks / valid_blocks) * 100.0
            outperf_ratio = (outperf_blocks  / valid_blocks) * 100.0
            score = (pos_ratio * 0.5) + (outperf_ratio * 0.5)

            upd.update({
                "positive_period_ratio":          pos_ratio,
                "benchmark_outperformance_ratio": outperf_ratio,
                "company_consistency_score":      score,
                "consistency_available":          True,
                "calculation_details":            calc_details,
            })
            values_to_update.append(upd)

        if not values_to_update:
            return

        self._disc.execute(update(CompanyTechnicalMetric), values_to_update)
        self._disc.commit()

def aggregate_group_consistency_periods(cons_eligible_comps: list) -> list:
    """
    Aggregates the historical periods across all eligible companies in a group.
    Returns a list of period dictionaries with median metrics.
    """
    periods_by_date = {}

    for c in cons_eligible_comps:
        calc_details = getattr(c, "calculation_details", None) or {}
        if isinstance(calc_details, str):
            import json
            try:
                calc_details = json.loads(calc_details)
            except Exception:
                calc_details = {}
        consistency = calc_details.get("consistency", {})
        periods = consistency.get("periods", [])

        for p in periods:
            if not p.get("available"):
                continue
            
            s_date = p["start_date"]
            e_date = p["end_date"]
            key = (s_date, e_date)
            
            if key not in periods_by_date:
                periods_by_date[key] = {
                    "start_date": s_date,
                    "end_date": e_date,
                    "company_returns": [],
                    "benchmark_returns": [],
                    "positives": 0,
                    "outperforms": 0,
                    "count": 0
                }
            
            grp = periods_by_date[key]
            grp["company_returns"].append(p.get("company_return", 0))
            grp["benchmark_returns"].append(p.get("benchmark_return", 0))
            if p.get("positive"): grp["positives"] += 1
            if p.get("outperformed"): grp["outperforms"] += 1
            grp["count"] += 1

    aggregated = []
    for key in sorted(periods_by_date.keys()):
        grp = periods_by_date[key]
        count = grp["count"]
        if count == 0:
            continue
            
        c_rets = grp["company_returns"]
        b_rets = grp["benchmark_returns"]
        
        c_rets_sorted = sorted(c_rets)
        mid = count // 2
        median_c = (c_rets_sorted[mid] + c_rets_sorted[~mid]) / 2.0
        
        b_rets_sorted = sorted(b_rets)
        median_b = (b_rets_sorted[mid] + b_rets_sorted[~mid]) / 2.0

        aggregated.append({
            "start_date": grp["start_date"],
            "end_date": grp["end_date"],
            "median_company_return": median_c,
            "median_benchmark_return": median_b,
            "positive_percentage": (grp["positives"] / count) * 100.0,
            "outperformed_percentage": (grp["outperforms"] / count) * 100.0,
            "constituent_count": count
        })
        
    return aggregated

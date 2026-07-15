"""
FundamentalProfitabilityService

Calculates raw company profitability metrics based on selected financial periods.
Uses plain Python with a single bulk P&L fetch — no Pandas / no NaN risk.
"""
from __future__ import annotations

import logging
import math
import uuid
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

import config
from models.discovery import CompanyFundamentalMetric
from services.fundamental.fundamental_period_selection import FundamentalPeriodSelectionService

logger = logging.getLogger(__name__)

STRONG_MARGIN_CHANGE_PP = getattr(config, "STRONG_MARGIN_CHANGE_PP", 3.0)
STABLE_MARGIN_CHANGE_PP = getattr(config, "STABLE_MARGIN_CHANGE_PP", 0.5)


def _safe(v) -> Optional[float]:
    try:
        if v is None:
            return None
        f = float(v)
        return None if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return None


def _get_margin_trend_status(change_pp: float) -> str:
    if change_pp >= STRONG_MARGIN_CHANGE_PP: return "STRONG_EXPANSION"
    if change_pp > STABLE_MARGIN_CHANGE_PP:  return "EXPANSION"
    if change_pp >= -STABLE_MARGIN_CHANGE_PP: return "STABLE"
    if change_pp > -STRONG_MARGIN_CHANGE_PP: return "CONTRACTION"
    return "STRONG_CONTRACTION"


class FundamentalProfitabilityService:
    def __init__(self, source_session: Session, discovery_session: Session):
        self._src = source_session
        self._disc = discovery_session
        self._period_svc = FundamentalPeriodSelectionService(self._src)

    def calculate_profitability(self, run_id: str) -> None:
        selections = self._period_svc.select_periods()
        if not selections:
            return

        # ── 1. Bulk hierarchy fetch ───────────────────────────────────────────
        h_rows = self._src.execute(
            text("SELECT id, sectore, industry, categorized_industry FROM companies")
        ).fetchall()
        hierarchy: dict[str, dict] = {
            str(r.id): {
                "sector": r.sectore or "",
                "industry": r.industry or "",
                "basic_industry": r.categorized_industry or "",
            }
            for r in h_rows
        }

        # ── 2. Bulk P&L fetch (sales + operating_profit) ─────────────────────
        overview_ids = [
            s["overview_id"] for s in selections
            if s["overview_id"] and s["profit_loss"]["comparable"]
        ]
        pl_data: dict = {}   # {str(overview_id): {period: (sales, op_profit)}}
        if overview_ids:
            pl_rows = self._src.execute(
                text("""
                    SELECT company_id, period, sales, operating_profit
                    FROM company_profit_losses
                    WHERE company_id = ANY(:cids)
                """),
                {"cids": overview_ids},
            ).fetchall()
            for r in pl_rows:
                cid = str(r.company_id)
                pl_data.setdefault(cid, {})[r.period] = (r.sales, r.operating_profit)

        # ── 3. Existing records ───────────────────────────────────────────────
        existing_records = self._disc.query(CompanyFundamentalMetric).filter_by(run_id=run_id).all()
        existing_map: dict[str, CompanyFundamentalMetric] = {
            r.source_company_id: r for r in existing_records
        }

        # ── 4. Process each company ───────────────────────────────────────────
        for s in selections:
            cid = s["source_company_id"]
            if not cid:
                continue

            hi = hierarchy.get(str(cid), {})
            warnings = list(s["warnings"])
            pl_info = s["profit_loss"]

            l_sales = None; l_op = None; p_sales = None; p_op = None
            l_margin = None; p_margin = None; margin_change_pp = None
            trend_status = "UNAVAILABLE"
            l_margin_avail = False; p_margin_avail = False; trend_avail = False

            if pl_info["comparable"] and s["overview_id"]:
                oid = str(s["overview_id"])
                periods = pl_data.get(oid, {})
                l_row = periods.get(pl_info["latest_period"])
                p_row = periods.get(pl_info["previous_period"])

                l_sales = l_row[0] if l_row else None
                l_op    = l_row[1] if l_row else None
                p_sales = p_row[0] if p_row else None
                p_op    = p_row[1] if p_row else None

                if l_sales is None: warnings.append("MISSING_LATEST_SALES")
                if l_op is None:    warnings.append("MISSING_LATEST_OPERATING_PROFIT")
                if l_sales is not None and l_op is not None:
                    if l_sales > 0:
                        l_margin = (l_op / l_sales) * 100.0
                        l_margin_avail = True
                    else:
                        warnings.append("INVALID_LATEST_SALES_BASE")

                if p_sales is None: warnings.append("MISSING_PREVIOUS_SALES")
                if p_op is None:    warnings.append("MISSING_PREVIOUS_OPERATING_PROFIT")
                if p_sales is not None and p_op is not None:
                    if p_sales > 0:
                        p_margin = (p_op / p_sales) * 100.0
                        p_margin_avail = True
                    else:
                        warnings.append("INVALID_PREVIOUS_SALES_BASE")

                if l_margin_avail and p_margin_avail:
                    margin_change_pp = l_margin - p_margin
                    trend_status = _get_margin_trend_status(margin_change_pp)
                    trend_avail = True
                else:
                    warnings.append("OPERATING_MARGIN_TREND_UNAVAILABLE")
            else:
                if not pl_info["comparable"]:
                    warnings.append("INSUFFICIENT_PROFIT_LOSS_PERIODS")
                    warnings.append("OPERATING_MARGIN_TREND_UNAVAILABLE")

            prof_detail = {
                "latest_period": pl_info["latest_period"],
                "previous_period": pl_info["previous_period"],
                "latest_sales": _safe(l_sales),
                "latest_operating_profit": _safe(l_op),
                "latest_operating_margin_pct": _safe(l_margin),
                "previous_sales": _safe(p_sales),
                "previous_operating_profit": _safe(p_op),
                "previous_operating_margin_pct": _safe(p_margin),
                "operating_margin_change_pp": _safe(margin_change_pp),
                "margin_trend_status": trend_status,
                "latest_operating_margin_available": l_margin_avail,
                "previous_operating_margin_available": p_margin_avail,
                "operating_margin_trend_available": trend_avail,
                "profitability_available": l_margin_avail,
            }
            unique_warnings = sorted(set(warnings))

            if cid in existing_map:
                rec = existing_map[cid]
                if not rec.sector and hi.get("sector"):      rec.sector = hi["sector"]
                if not rec.industry and hi.get("industry"):  rec.industry = hi["industry"]
                if not rec.basic_industry and hi.get("basic_industry"): rec.basic_industry = hi["basic_industry"]
                existing_calc = dict(rec.calculation_details or {})
                existing_calc["profitability"] = prof_detail
                existing_calc["warnings"] = sorted(set(
                    existing_calc.get("warnings", []) + unique_warnings
                ))
                rec.calculation_details = existing_calc
            else:
                # Company was not in the universe snapshot (missing tech data) — skip
                continue

        self._disc.commit()

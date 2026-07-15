"""
FundamentalGrowthService

Calculates raw company growth metrics based on selected financial periods.
Uses plain Python with a single bulk P&L fetch — no Pandas / no NaN risk.
"""
from __future__ import annotations

import logging
import math
import uuid
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from models.discovery import CompanyFundamentalMetric
from services.fundamental.fundamental_period_selection import FundamentalPeriodSelectionService

logger = logging.getLogger(__name__)


def _safe(v) -> Optional[float]:
    try:
        if v is None:
            return None
        f = float(v)
        return None if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return None


def _get_np_transition(prev: float, latest: float) -> str:
    if prev > 0:
        return "STANDARD_GROWTH"
    if prev < 0:
        if latest > 0: return "LOSS_TO_PROFIT"
        if latest > prev: return "LOSS_NARROWED"
        if latest < prev: return "LOSS_WIDENED"
        return "LOSS_UNCHANGED"
    if latest > 0: return "ZERO_BASE_TO_PROFIT"
    if latest < 0: return "ZERO_BASE_TO_LOSS"
    return "ZERO_BASE_UNCHANGED"


class FundamentalGrowthService:
    def __init__(self, source_session: Session, discovery_session: Session):
        self._src = source_session
        self._disc = discovery_session
        self._period_svc = FundamentalPeriodSelectionService(self._src)

    def calculate_growth(self, run_id: str) -> None:
        selections = self._period_svc.select_periods()
        if not selections:
            return

        # ── 1. Bulk hierarchy fetch (id → {sector, industry, basic_industry}) ──
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

        # ── 2. Bulk P&L fetch for all overview_ids in one shot ────────────────
        overview_ids = [
            s["overview_id"] for s in selections
            if s["overview_id"] and s["profit_loss"]["comparable"]
        ]
        # pl_data: {overview_id: {period: (sales, net_profit)}}
        pl_data: dict = {}
        if overview_ids:
            pl_rows = self._src.execute(
                text("""
                    SELECT company_id, period, sales, net_profit
                    FROM company_profit_losses
                    WHERE company_id = ANY(:cids)
                """),
                {"cids": overview_ids},
            ).fetchall()
            for r in pl_rows:
                cid = str(r.company_id)
                pl_data.setdefault(cid, {})[r.period] = (r.sales, r.net_profit)

        # ── 3. Fetch existing DB records for this run ─────────────────────────
        existing_records = self._disc.query(CompanyFundamentalMetric).filter_by(run_id=run_id).all()
        existing_map: dict[str, CompanyFundamentalMetric] = {
            r.source_company_id: r for r in existing_records
        }

        # ── 4. Process each company in plain Python ───────────────────────────
        for s in selections:
            cid = s["source_company_id"]
            if not cid:
                continue

            hi = hierarchy.get(str(cid), {})
            warnings = list(s["warnings"])
            pl_info = s["profit_loss"]

            sales_growth_pct = None
            sales_growth_available = False
            np_growth_pct = None
            np_growth_available = False
            np_transition = None

            if pl_info["comparable"] and s["overview_id"]:
                oid = str(s["overview_id"])
                periods = pl_data.get(oid, {})
                l_period = pl_info["latest_period"]
                p_period = pl_info["previous_period"]

                l_row = periods.get(l_period)
                p_row = periods.get(p_period)

                l_sales = l_row[0] if l_row else None
                p_sales = p_row[0] if p_row else None
                l_np    = l_row[1] if l_row else None
                p_np    = p_row[1] if p_row else None

                if l_sales is None: warnings.append("MISSING_LATEST_SALES")
                if p_sales is None: warnings.append("MISSING_PREVIOUS_SALES")
                if l_sales is not None and p_sales is not None:
                    if p_sales > 0:
                        sales_growth_pct = ((l_sales / p_sales) - 1.0) * 100.0
                        sales_growth_available = True
                    else:
                        warnings.append("INVALID_PREVIOUS_SALES_BASE")

                if l_np is None: warnings.append("MISSING_LATEST_NET_PROFIT")
                if p_np is None: warnings.append("MISSING_PREVIOUS_NET_PROFIT")
                if l_np is not None and p_np is not None:
                    np_transition = _get_np_transition(p_np, l_np)
                    if p_np > 0:
                        np_growth_pct = ((l_np / p_np) - 1.0) * 100.0
                        np_growth_available = True
                    else:
                        warnings.append("NON_STANDARD_NET_PROFIT_BASE")
            else:
                if not pl_info["comparable"]:
                    warnings.append("INSUFFICIENT_PROFIT_LOSS_PERIODS")

            growth_detail = {
                "latest_period": pl_info["latest_period"],
                "previous_period": pl_info["previous_period"],
                "sales_growth_available": sales_growth_available,
                "sales_growth_pct": _safe(sales_growth_pct),
                "net_profit_growth_available": np_growth_available,
                "net_profit_growth_pct": _safe(np_growth_pct),
                "net_profit_transition": np_transition,
                "growth_available": bool(sales_growth_available or np_growth_available),
            }
            unique_warnings = sorted(set(warnings))

            if cid in existing_map:
                rec = existing_map[cid]
                rec.sector = rec.sector or hi.get("sector", "")
                rec.industry = rec.industry or hi.get("industry", "")
                rec.basic_industry = rec.basic_industry or hi.get("basic_industry", "")
                existing_calc = dict(rec.calculation_details or {})
                existing_calc["growth"] = growth_detail
                existing_calc["warnings"] = sorted(set(
                    existing_calc.get("warnings", []) + unique_warnings
                ))
                rec.calculation_details = existing_calc
            else:
                # Company was not in the universe snapshot (missing tech data) — skip
                continue

        self._disc.commit()

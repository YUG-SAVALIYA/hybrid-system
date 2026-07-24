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


def _is_finite(v) -> bool:
    try:
        if v is None:
            return False
        f = float(v)
        return not (math.isnan(f) or math.isinf(f))
    except (TypeError, ValueError):
        return False


def _get_np_transition(prev: float, latest: float) -> str:
    if prev > 0:
        return "STANDARD_GROWTH"
    if prev < 0:
        if latest > 0: return "LOSS_TO_PROFIT"
        if latest > prev: return "LOSS_NARROWED"
        if latest < prev: return "LOSS_WIDENED"
        return "LOSS_UNCHANGED"
    if latest > 0: return "ZERO_TO_PROFIT"
    if latest < 0: return "ZERO_TO_LOSS"
    return "ZERO_UNCHANGED"


def _eval_net_profit_growth(latest, prev):
    if not (_is_finite(latest) and _is_finite(prev)):
        return None, "UNAVAILABLE", False
    l_f, p_f = float(latest), float(prev)
    transition = _get_np_transition(p_f, l_f)
    if p_f > 0:
        pct = round(((l_f - p_f) / p_f) * 100.0, 2)
        return pct, transition, True
    return None, transition, False


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
            str(s["overview_id"]) for s in selections
            if s.get("overview_id") and s["profit_loss"]["comparable"]
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
            str(r.source_company_id): r for r in existing_records
        }

        # ── 4. Process each company in plain Python ───────────────────────────
        for s in selections:
            cid = s["source_company_id"]
            if not cid:
                continue

            str_cid = str(cid)
            hi = hierarchy.get(str_cid, {})
            warnings = list(s["warnings"])
            pl_info = s["profit_loss"]

            sales_growth_pct = None
            sales_growth_available = False
            np_growth_pct = None
            np_growth_available = False
            np_transition = None

            if pl_info["comparable"] and s.get("overview_id"):
                oid = str(s["overview_id"])
                periods = pl_data.get(oid, {})
                l_period = pl_info["latest_period"]
                p_period = pl_info["previous_period"]

                if l_period in periods and p_period in periods:
                    l_sales, l_np = periods[l_period]
                    p_sales, p_np = periods[p_period]

                    if _is_finite(l_sales) and _is_finite(p_sales) and float(p_sales) > 0:
                        sales_growth_pct = round(((float(l_sales) - float(p_sales)) / float(p_sales)) * 100.0, 2)
                        sales_growth_available = True

                    np_growth_pct, np_transition, np_growth_available = _eval_net_profit_growth(l_np, p_np)
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

            if str_cid in existing_map:
                rec = existing_map[str_cid]
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
                new_rec = CompanyFundamentalMetric(
                    id=str(uuid.uuid4()),
                    run_id=run_id,
                    source_company_id=str_cid,
                    symbol=s.get("symbol", ""),
                    sector=hi.get("sector", ""),
                    industry=hi.get("industry", ""),
                    basic_industry=hi.get("basic_industry", ""),
                    calculation_details={"growth": growth_detail, "warnings": unique_warnings},
                )
                self._disc.add(new_rec)
                existing_map[str_cid] = new_rec

        self._disc.commit()

"""
FundamentalFinancialStrengthService

Calculates raw company financial strength metrics based on selected balance-sheet periods.
Uses plain Python with a single bulk balance-sheet fetch — no Pandas / no NaN risk.
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
from services.common.sector_classifier import SectorClassifier

logger = logging.getLogger(__name__)


def _safe(v) -> Optional[float]:
    try:
        if v is None:
            return None
        f = float(v)
        return None if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return None


def _get_borrowing_transition(prev: float, latest: float) -> str:
    if latest < 0 or prev < 0:
        return "INVALID_NEGATIVE_BORROWINGS"
    if prev == 0 and latest > 0:   return "ZERO_TO_POSITIVE"
    if prev == 0 and latest == 0:  return "ZERO_TO_ZERO"
    if latest > prev:              return "INCREASED"
    if latest < prev:              return "DECREASED"
    return "UNCHANGED"


class FundamentalFinancialStrengthService:
    def __init__(self, source_session: Session, discovery_session: Session):
        self._src = source_session
        self._disc = discovery_session
        self._period_svc = FundamentalPeriodSelectionService(self._src)

    def calculate_financial_strength(self, run_id: str) -> None:
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

        # ── 2. Bulk balance-sheet fetch ───────────────────────────────────────
        overview_ids = [
            s["overview_id"] for s in selections
            if s["overview_id"] and s["balance_sheet"]["comparable"]
        ]
        bs_data: dict = {}   # {str(overview_id): {period: (ec, reserves, borrowings)}}
        if overview_ids:
            bs_rows = self._src.execute(
                text("""
                    SELECT company_id, period, equity_capital, reserves, borrowings
                    FROM company_balance_sheets
                    WHERE company_id = ANY(:cids)
                """),
                {"cids": overview_ids},
            ).fetchall()
            for r in bs_rows:
                cid = str(r.company_id)
                bs_data.setdefault(cid, {})[r.period] = (
                    r.equity_capital, r.reserves, r.borrowings
                )

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
            bs_info = s["balance_sheet"]

            sector = hi.get("sector", "")
            industry = hi.get("industry", "")
            basic_industry = hi.get("basic_industry", "")

            is_financial = SectorClassifier.is_financial_business(sector, industry, basic_industry)
            std_debt_applicable = not is_financial
            bus_classification = "EXCLUDED_FINANCIAL" if is_financial else "STANDARD_NON_FINANCIAL"

            l_ec = None; l_res = None; l_borr = None
            p_ec = None; p_res = None; p_borr = None
            l_eq = None; p_eq = None; dte = None
            borr_chg_abs = None; borr_chg_pct = None
            borr_transition = "UNAVAILABLE"
            l_eq_avail = False; p_eq_avail = False
            dte_avail = False; trend_avail = False; fs_avail = False

            if bs_info["comparable"] and s["overview_id"]:
                oid = str(s["overview_id"])
                periods = bs_data.get(oid, {})
                l_row = periods.get(bs_info["latest_period"])
                p_row = periods.get(bs_info["previous_period"])

                l_ec   = l_row[0] if l_row else None
                l_res  = l_row[1] if l_row else None
                l_borr = l_row[2] if l_row else None
                p_ec   = p_row[0] if p_row else None
                p_res  = p_row[1] if p_row else None
                p_borr = p_row[2] if p_row else None

                # Latest equity
                if l_ec is None:  warnings.append("MISSING_LATEST_EQUITY_CAPITAL")
                if l_res is None: warnings.append("MISSING_LATEST_RESERVES")
                if l_ec is not None and l_res is not None:
                    l_eq = l_ec + l_res
                    l_eq_avail = True

                # Previous equity
                if p_ec is None:  warnings.append("MISSING_PREVIOUS_EQUITY_CAPITAL")
                if p_res is None: warnings.append("MISSING_PREVIOUS_RESERVES")
                if p_ec is not None and p_res is not None:
                    p_eq = p_ec + p_res
                    p_eq_avail = True

                if l_borr is None: warnings.append("MISSING_LATEST_BORROWINGS")
                if p_borr is None: warnings.append("MISSING_PREVIOUS_BORROWINGS")

                # Debt-to-equity
                if not std_debt_applicable:
                    warnings.append("STANDARD_DEBT_RULE_NOT_APPLICABLE")
                else:
                    if l_borr is not None and l_borr < 0:
                        warnings.append("INVALID_NEGATIVE_BORROWINGS")
                    elif l_borr is not None and l_eq_avail:
                        if l_eq > 0:
                            dte = l_borr / l_eq
                            dte_avail = True
                        else:
                            warnings.append("NON_POSITIVE_LATEST_EQUITY")

                # Borrowing trend
                if l_borr is not None and p_borr is not None:
                    if l_borr < 0 or p_borr < 0:
                        warnings.append("INVALID_NEGATIVE_BORROWINGS")
                        borr_transition = "INVALID_NEGATIVE_BORROWINGS"
                    else:
                        borr_chg_abs = l_borr - p_borr
                        borr_transition = _get_borrowing_transition(p_borr, l_borr)
                        if p_borr > 0:
                            borr_chg_pct = ((l_borr / p_borr) - 1.0) * 100.0
                            trend_avail = True
                        else:
                            warnings.append("BORROWING_PERCENTAGE_CHANGE_UNAVAILABLE")

                l_borr_valid = l_borr is not None and l_borr >= 0
                fs_avail = l_eq_avail and l_borr_valid
            else:
                if not bs_info["comparable"]:
                    warnings.append("INSUFFICIENT_BALANCE_SHEET_PERIODS")

            fs_detail = {
                "latest_period": bs_info["latest_period"],
                "previous_period": bs_info["previous_period"],
                "business_classification": bus_classification,
                "standard_debt_rule_applicable": std_debt_applicable,
                "latest": {
                    "equity_capital": _safe(l_ec),
                    "reserves": _safe(l_res),
                    "equity": _safe(l_eq),
                    "borrowings": _safe(l_borr),
                },
                "previous": {
                    "equity_capital": _safe(p_ec),
                    "reserves": _safe(p_res),
                    "equity": _safe(p_eq),
                    "borrowings": _safe(p_borr),
                },
                "debt_to_equity": _safe(dte),
                "borrowing_change_absolute": _safe(borr_chg_abs),
                "borrowing_change_pct": _safe(borr_chg_pct),
                "borrowing_transition": borr_transition,
                "latest_equity_available": l_eq_avail,
                "previous_equity_available": p_eq_avail,
                "debt_to_equity_available": dte_avail,
                "borrowing_trend_available": trend_avail,
                "financial_strength_available": fs_avail,
            }
            unique_warnings = sorted(set(warnings))

            if cid in existing_map:
                rec = existing_map[cid]
                if not rec.sector and sector:              rec.sector = sector
                if not rec.industry and industry:          rec.industry = industry
                if not rec.basic_industry and basic_industry: rec.basic_industry = basic_industry
                existing_calc = dict(rec.calculation_details or {})
                existing_calc["financial_strength"] = fs_detail
                existing_calc["warnings"] = sorted(set(
                    existing_calc.get("warnings", []) + unique_warnings
                ))
                rec.calculation_details = existing_calc
            else:
                # Company was not in the universe snapshot (missing tech data) — skip
                continue

        self._disc.commit()

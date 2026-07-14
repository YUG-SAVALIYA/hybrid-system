"""
FundamentalFinancialStrengthService

Calculates raw company financial strength metrics based on selected balance-sheet periods.
"""
from __future__ import annotations

import logging
import uuid
from sqlalchemy import text
from sqlalchemy.orm import Session

from models.discovery import CompanyFundamentalMetric
from services.fundamental.fundamental_period_selection import FundamentalPeriodSelectionService
from services.common.sector_classifier import SectorClassifier

logger = logging.getLogger(__name__)

def _get_borrowing_transition(prev: float, latest: float) -> str:
    if latest < 0 or prev < 0:
        return "INVALID_NEGATIVE_BORROWINGS"
    if prev == 0 and latest > 0:
        return "ZERO_TO_POSITIVE"
    if prev == 0 and latest == 0:
        return "ZERO_TO_ZERO"
    if latest > prev:
        return "INCREASED"
    if latest < prev:
        return "DECREASED"
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

        overview_ids = [s["overview_id"] for s in selections if s["overview_id"] and s["balance_sheet"]["comparable"]]
        
        bs_data_map = {}
        if overview_ids:
            bs_query = text("""
                SELECT company_id, period, equity_capital, reserves, borrowings
                FROM company_balance_sheets
                WHERE company_id = ANY(:cids)
            """)
            bs_records = self._src.execute(bs_query, {"cids": overview_ids}).fetchall()
            for r in bs_records:
                cid = r.company_id
                if cid not in bs_data_map:
                    bs_data_map[cid] = {}
                bs_data_map[cid][r.period] = {
                    "equity_capital": r.equity_capital,
                    "reserves": r.reserves,
                    "borrowings": r.borrowings
                }

        hierarchy_query = text("SELECT id, share_symbol, sectore, industry, categorized_industry FROM companies")
        h_records = self._src.execute(hierarchy_query).fetchall()
        h_map = {r.id: r for r in h_records}

        values_to_upsert = []
        for sel in selections:
            source_comp_id = sel["source_company_id"]
            if not source_comp_id:
                continue

            symbol = sel["symbol"]
            overview_id = sel["overview_id"]
            warnings = set(sel["warnings"])
            
            bs_comp = sel["balance_sheet"]["comparable"]
            latest_period = sel["balance_sheet"]["latest_period"]
            prev_period = sel["balance_sheet"]["previous_period"]

            hr = h_map.get(source_comp_id)
            sector = hr.sectore if hr else None
            industry = hr.industry if hr else None
            basic_industry = hr.categorized_industry if hr else None

            is_financial = SectorClassifier.is_financial_business(sector, industry, basic_industry)
            std_debt_applicable = not is_financial
            bus_classification = "EXCLUDED_FINANCIAL" if is_financial else "STANDARD_NON_FINANCIAL"

            l_ec = None; l_res = None; l_eq = None; l_borr = None
            p_ec = None; p_res = None; p_eq = None; p_borr = None
            
            dte = None
            borr_chg_abs = None
            borr_chg_pct = None
            borr_transition = "UNAVAILABLE"
            
            l_eq_avail = False
            p_eq_avail = False
            dte_avail = False
            trend_avail = False
            
            fs_avail = False

            if bs_comp and overview_id and overview_id in bs_data_map:
                l_data = bs_data_map[overview_id].get(latest_period, {})
                p_data = bs_data_map[overview_id].get(prev_period, {})

                l_ec = l_data.get("equity_capital")
                l_res = l_data.get("reserves")
                l_borr = l_data.get("borrowings")
                
                p_ec = p_data.get("equity_capital")
                p_res = p_data.get("reserves")
                p_borr = p_data.get("borrowings")

                # Latest Equity
                if l_ec is None: warnings.add("MISSING_LATEST_EQUITY_CAPITAL")
                if l_res is None: warnings.add("MISSING_LATEST_RESERVES")
                if l_ec is not None and l_res is not None:
                    l_eq = l_ec + l_res
                    l_eq_avail = True

                # Previous Equity
                if p_ec is None: warnings.add("MISSING_PREVIOUS_EQUITY_CAPITAL")
                if p_res is None: warnings.add("MISSING_PREVIOUS_RESERVES")
                if p_ec is not None and p_res is not None:
                    p_eq = p_ec + p_res
                    p_eq_avail = True

                if l_borr is None: warnings.add("MISSING_LATEST_BORROWINGS")
                if p_borr is None: warnings.add("MISSING_PREVIOUS_BORROWINGS")

                # Debt to Equity
                if not std_debt_applicable:
                    warnings.add("STANDARD_DEBT_RULE_NOT_APPLICABLE")
                else:
                    if l_borr is not None and l_borr < 0:
                        warnings.add("INVALID_NEGATIVE_BORROWINGS")
                    elif l_borr is not None and l_eq_avail:
                        if l_eq > 0:
                            dte = l_borr / l_eq
                            dte_avail = True
                        else:
                            warnings.add("NON_POSITIVE_LATEST_EQUITY")
                
                # Borrowing Trend
                if l_borr is not None and p_borr is not None:
                    if l_borr < 0 or p_borr < 0:
                        warnings.add("INVALID_NEGATIVE_BORROWINGS")
                        borr_transition = "INVALID_NEGATIVE_BORROWINGS"
                    else:
                        borr_chg_abs = l_borr - p_borr
                        borr_transition = _get_borrowing_transition(p_borr, l_borr)
                        if p_borr > 0:
                            borr_chg_pct = ((l_borr / p_borr) - 1.0) * 100.0
                            trend_avail = True
                        else:
                            warnings.add("BORROWING_PERCENTAGE_CHANGE_UNAVAILABLE")

                # Overall Availability
                # "financial_strength_available = latest_equity_available AND latest_borrowings is valid"
                # For excluded financial businesses, this can remain true even though DTE is not applicable.
                l_borr_valid = (l_borr is not None and l_borr >= 0)
                fs_avail = (l_eq_avail and l_borr_valid)
            else:
                if not bs_comp:
                    warnings.add("INSUFFICIENT_BALANCE_SHEET_PERIODS")

            calc_details = {
                "financial_strength": {
                    "latest_period": latest_period,
                    "previous_period": prev_period,
                    "business_classification": bus_classification,
                    "standard_debt_rule_applicable": std_debt_applicable,
                    "latest": {
                        "equity_capital": l_ec,
                        "reserves": l_res,
                        "equity": l_eq,
                        "borrowings": l_borr
                    },
                    "previous": {
                        "equity_capital": p_ec,
                        "reserves": p_res,
                        "equity": p_eq,
                        "borrowings": p_borr
                    },
                    "debt_to_equity": dte,
                    "borrowing_change_absolute": borr_chg_abs,
                    "borrowing_change_pct": borr_chg_pct,
                    "borrowing_transition": borr_transition,
                    "latest_equity_available": l_eq_avail,
                    "previous_equity_available": p_eq_avail,
                    "debt_to_equity_available": dte_avail,
                    "borrowing_trend_available": trend_avail,
                    "financial_strength_available": fs_avail
                },
                "warnings": sorted(list(warnings))
            }

            values_to_upsert.append({
                "id": str(uuid.uuid4()),
                "run_id": run_id,
                "source_company_id": source_comp_id,
                "symbol": symbol,
                "sector": sector,
                "industry": industry,
                "basic_industry": basic_industry,
                "calculation_details": calc_details
            })

        if not values_to_upsert:
            return

        existing_records = self._disc.query(CompanyFundamentalMetric).filter_by(run_id=run_id).all()
        existing_map = {r.source_company_id: r for r in existing_records}

        for v in values_to_upsert:
            cid = v["source_company_id"]
            if cid in existing_map:
                rec = existing_map[cid]
                if not rec.sector and v["sector"]: rec.sector = v["sector"]
                if not rec.industry and v["industry"]: rec.industry = v["industry"]
                if not rec.basic_industry and v["basic_industry"]: rec.basic_industry = v["basic_industry"]
                
                existing_calc = dict(rec.calculation_details) if rec.calculation_details else {}
                existing_calc["financial_strength"] = v["calculation_details"]["financial_strength"]
                
                old_warn = existing_calc.get("warnings", [])
                new_warn = list(set(old_warn + v["calculation_details"]["warnings"]))
                new_warn.sort()
                existing_calc["warnings"] = new_warn
                
                rec.calculation_details = existing_calc
            else:
                new_rec = CompanyFundamentalMetric(
                    id=v["id"],
                    run_id=v["run_id"],
                    source_company_id=v["source_company_id"],
                    symbol=v["symbol"],
                    sector=v["sector"],
                    industry=v["industry"],
                    basic_industry=v["basic_industry"],
                    calculation_details=v["calculation_details"]
                )
                self._disc.add(new_rec)
        
        self._disc.commit()

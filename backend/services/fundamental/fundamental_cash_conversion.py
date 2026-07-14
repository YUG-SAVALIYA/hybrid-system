"""
FundamentalCashConversionService

Calculates raw company operating-cash-flow-to-net-profit metrics based on common periods.
"""
from __future__ import annotations

import logging
import uuid
from sqlalchemy import text
from sqlalchemy.orm import Session

import config
from models.discovery import CompanyFundamentalMetric
from services.fundamental.fundamental_period_selection import FundamentalPeriodSelectionService

logger = logging.getLogger(__name__)

CASH_CONVERSION_STABLE_TOLERANCE = getattr(config, 'CASH_CONVERSION_STABLE_TOLERANCE', 0.10)

def _get_cc_status(pat: float, ocf: float) -> str:
    if pat > 0:
        if ocf == 0: return "PROFIT_WITH_ZERO_OPERATING_CASH_FLOW"
        if ocf < 0: return "NEGATIVE_OPERATING_CASH_FLOW"
        ratio = ocf / pat
        if ratio >= 1.0: return "STRONG_CASH_CONVERSION"
        if ratio >= 0.5: return "ADEQUATE_CASH_CONVERSION"
        return "WEAK_CASH_CONVERSION"
    elif pat < 0:
        if ocf > 0: return "LOSS_WITH_POSITIVE_OPERATING_CASH_FLOW"
        if ocf < 0: return "LOSS_WITH_NEGATIVE_OPERATING_CASH_FLOW"
        return "LOSS_WITH_ZERO_OPERATING_CASH_FLOW"
    else: # pat == 0
        if ocf > 0: return "ZERO_PAT_WITH_POSITIVE_OPERATING_CASH_FLOW"
        if ocf < 0: return "ZERO_PAT_WITH_NEGATIVE_OPERATING_CASH_FLOW"
        return "ZERO_PAT_WITH_ZERO_OPERATING_CASH_FLOW"

def _get_cc_trend_status(change: float) -> str:
    if change > CASH_CONVERSION_STABLE_TOLERANCE: return "IMPROVED"
    if change >= -CASH_CONVERSION_STABLE_TOLERANCE: return "STABLE"
    return "DETERIORATED"


class FundamentalCashConversionService:
    def __init__(self, source_session: Session, discovery_session: Session):
        self._src = source_session
        self._disc = discovery_session
        self._period_svc = FundamentalPeriodSelectionService(self._src)

    def calculate_cash_conversion(self, run_id: str) -> None:
        selections = self._period_svc.select_periods()
        if not selections:
            return

        overview_ids = [
            s["overview_id"] for s in selections 
            if s["overview_id"] and s["profit_loss_cash_flow_common"]["comparable"]
        ]
        
        pl_data_map = {}
        cf_data_map = {}

        if overview_ids:
            pl_query = text("""
                SELECT company_id, period, net_profit
                FROM company_profit_losses
                WHERE company_id = ANY(:cids)
            """)
            pl_records = self._src.execute(pl_query, {"cids": overview_ids}).fetchall()
            for r in pl_records:
                cid = r.company_id
                if cid not in pl_data_map:
                    pl_data_map[cid] = {}
                pl_data_map[cid][r.period] = r.net_profit

            cf_query = text("""
                SELECT company_id, period, cash_from_operating_activity
                FROM company_cash_flows
                WHERE company_id = ANY(:cids)
            """)
            cf_records = self._src.execute(cf_query, {"cids": overview_ids}).fetchall()
            for r in cf_records:
                cid = r.company_id
                if cid not in cf_data_map:
                    cf_data_map[cid] = {}
                cf_data_map[cid][r.period] = r.cash_from_operating_activity

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
            
            # Start with warnings from the period selection natively, but only keeping CC relevant warnings?
            # Wait, period_svc returns warnings globally for a company. 
            # We want to preserve those or append new ones. 
            # Let's extract the ones we want to log if needed.
            warnings = set(sel["warnings"])
            
            common_comp = sel["profit_loss_cash_flow_common"]["comparable"]
            latest_period = sel["profit_loss_cash_flow_common"]["latest_period"]
            prev_period = sel["profit_loss_cash_flow_common"]["previous_period"]

            hr = h_map.get(source_comp_id)
            sector = hr.sectore if hr else None
            industry = hr.industry if hr else None
            basic_industry = hr.categorized_industry if hr else None

            l_pat = None; l_ocf = None
            p_pat = None; p_ocf = None
            
            l_ratio = None
            p_ratio = None
            l_status = "UNAVAILABLE"
            p_status = "UNAVAILABLE"
            
            ratio_change = None
            trend_status = "UNAVAILABLE"
            
            l_avail = False
            p_avail = False
            trend_avail = False
            
            cc_avail = False

            if common_comp and overview_id and overview_id in pl_data_map and overview_id in cf_data_map:
                l_pat = pl_data_map[overview_id].get(latest_period)
                p_pat = pl_data_map[overview_id].get(prev_period)
                
                l_ocf = cf_data_map[overview_id].get(latest_period)
                p_ocf = cf_data_map[overview_id].get(prev_period)
                
                cc_avail = (l_pat is not None and l_ocf is not None)

                # Latest Ratio
                if l_pat is None: warnings.add("MISSING_LATEST_NET_PROFIT")
                if l_ocf is None: warnings.add("MISSING_LATEST_OPERATING_CASH_FLOW")
                if l_pat is not None and l_ocf is not None:
                    l_status = _get_cc_status(l_pat, l_ocf)
                    if l_pat > 0:
                        l_ratio = l_ocf / l_pat
                        l_avail = True
                    else:
                        warnings.add("NON_POSITIVE_LATEST_PAT_BASE")

                # Previous Ratio
                if p_pat is None: warnings.add("MISSING_PREVIOUS_NET_PROFIT")
                if p_ocf is None: warnings.add("MISSING_PREVIOUS_OPERATING_CASH_FLOW")
                if p_pat is not None and p_ocf is not None:
                    p_status = _get_cc_status(p_pat, p_ocf)
                    if p_pat > 0:
                        p_ratio = p_ocf / p_pat
                        p_avail = True
                    else:
                        warnings.add("NON_POSITIVE_PREVIOUS_PAT_BASE")
                
                # Trend
                if l_avail and p_avail:
                    ratio_change = round(l_ratio - p_ratio, 4)
                    trend_status = _get_cc_trend_status(ratio_change)
                    trend_avail = True
                else:
                    warnings.add("CASH_CONVERSION_TREND_UNAVAILABLE")

            else:
                if not common_comp:
                    # If periods exist but not comparable due to non-consecutive
                    if "NON_CONSECUTIVE_ANNUAL_PERIODS" in warnings:
                        # Already in warnings, don't add NO_COMMON_PL_CF_PERIOD incorrectly
                        pass
                    elif "NO_COMMON_PL_CF_PERIOD" not in warnings:
                        # Add it if missing
                        warnings.add("NO_COMMON_PL_CF_PERIOD")
                    
                    warnings.add("CASH_CONVERSION_TREND_UNAVAILABLE")

            calc_details = {
                "earnings_quality": {
                    "cash_conversion": {
                        "latest_period": latest_period,
                        "previous_period": prev_period,
                        "latest": {
                            "net_profit": l_pat,
                            "operating_cash_flow": l_ocf,
                            "ocf_to_pat": l_ratio,
                            "status": l_status
                        },
                        "previous": {
                            "net_profit": p_pat,
                            "operating_cash_flow": p_ocf,
                            "ocf_to_pat": p_ratio,
                            "status": p_status
                        },
                        "ocf_to_pat_change": ratio_change,
                        "trend_status": trend_status,
                        "latest_cash_conversion_available": l_avail,
                        "previous_cash_conversion_available": p_avail,
                        "cash_conversion_trend_available": trend_avail,
                        "cash_conversion_available": cc_avail
                    }
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

        import copy
        for v in values_to_upsert:
            cid = v["source_company_id"]
            if cid in existing_map:
                rec = existing_map[cid]
                if not rec.sector and v["sector"]: rec.sector = v["sector"]
                if not rec.industry and v["industry"]: rec.industry = v["industry"]
                if not rec.basic_industry and v["basic_industry"]: rec.basic_industry = v["basic_industry"]
                
                existing_calc = copy.deepcopy(rec.calculation_details) if rec.calculation_details else {}
                
                # Deep merge earnings_quality
                eq = existing_calc.get("earnings_quality", {})
                eq["cash_conversion"] = v["calculation_details"]["earnings_quality"]["cash_conversion"]
                existing_calc["earnings_quality"] = eq
                
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

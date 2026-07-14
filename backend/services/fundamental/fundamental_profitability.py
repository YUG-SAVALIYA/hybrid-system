"""
FundamentalProfitabilityService

Calculates raw company profitability metrics based on selected financial periods.
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

# Constants to be placed in config, but using defaults if not present
STRONG_MARGIN_CHANGE_PP = getattr(config, 'STRONG_MARGIN_CHANGE_PP', 3.0)
STABLE_MARGIN_CHANGE_PP = getattr(config, 'STABLE_MARGIN_CHANGE_PP', 0.5)

def _get_margin_trend_status(change_pp: float) -> str:
    if change_pp >= STRONG_MARGIN_CHANGE_PP: return "STRONG_EXPANSION"
    if change_pp > STABLE_MARGIN_CHANGE_PP: return "EXPANSION"
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

        overview_ids = [s["overview_id"] for s in selections if s["overview_id"] and s["profit_loss"]["comparable"]]
        
        pl_data_map = {}
        if overview_ids:
            pl_query = text("""
                SELECT company_id, period, sales, operating_profit
                FROM company_profit_losses
                WHERE company_id = ANY(:cids)
            """)
            pl_records = self._src.execute(pl_query, {"cids": overview_ids}).fetchall()
            for r in pl_records:
                cid = r.company_id
                if cid not in pl_data_map:
                    pl_data_map[cid] = {}
                pl_data_map[cid][r.period] = {
                    "sales": r.sales,
                    "operating_profit": r.operating_profit
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
            
            pl_comp = sel["profit_loss"]["comparable"]
            latest_period = sel["profit_loss"]["latest_period"]
            prev_period = sel["profit_loss"]["previous_period"]
            
            l_sales = None
            l_op = None
            p_sales = None
            p_op = None
            
            l_margin = None
            p_margin = None
            margin_change_pp = None
            trend_status = "UNAVAILABLE"
            
            l_margin_avail = False
            p_margin_avail = False
            trend_avail = False

            if pl_comp and overview_id and overview_id in pl_data_map:
                latest_data = pl_data_map[overview_id].get(latest_period, {})
                prev_data = pl_data_map[overview_id].get(prev_period, {})

                l_sales = latest_data.get("sales")
                l_op = latest_data.get("operating_profit")
                p_sales = prev_data.get("sales")
                p_op = prev_data.get("operating_profit")

                # Latest margin
                if l_sales is None: warnings.add("MISSING_LATEST_SALES")
                if l_op is None: warnings.add("MISSING_LATEST_OPERATING_PROFIT")
                
                if l_sales is not None and l_op is not None:
                    if l_sales > 0:
                        l_margin = (l_op / l_sales) * 100.0
                        l_margin_avail = True
                    else:
                        warnings.add("INVALID_LATEST_SALES_BASE")

                # Previous margin
                if p_sales is None: warnings.add("MISSING_PREVIOUS_SALES")
                if p_op is None: warnings.add("MISSING_PREVIOUS_OPERATING_PROFIT")

                if p_sales is not None and p_op is not None:
                    if p_sales > 0:
                        p_margin = (p_op / p_sales) * 100.0
                        p_margin_avail = True
                    else:
                        warnings.add("INVALID_PREVIOUS_SALES_BASE")

                if l_margin_avail and p_margin_avail:
                    margin_change_pp = l_margin - p_margin
                    trend_status = _get_margin_trend_status(margin_change_pp)
                    trend_avail = True
                else:
                    warnings.add("OPERATING_MARGIN_TREND_UNAVAILABLE")

            else:
                if not pl_comp:
                    warnings.add("INSUFFICIENT_PROFIT_LOSS_PERIODS")
                    warnings.add("OPERATING_MARGIN_TREND_UNAVAILABLE")

            hr = h_map.get(source_comp_id)
            sector = hr.sectore if hr else None
            industry = hr.industry if hr else None
            basic_industry = hr.categorized_industry if hr else None

            calc_details = {
                "profitability": {
                    "latest_period": latest_period,
                    "previous_period": prev_period,
                    "latest_sales": l_sales,
                    "latest_operating_profit": l_op,
                    "latest_operating_margin_pct": l_margin,
                    "previous_sales": p_sales,
                    "previous_operating_profit": p_op,
                    "previous_operating_margin_pct": p_margin,
                    "operating_margin_change_pp": margin_change_pp,
                    "margin_trend_status": trend_status,
                    "latest_operating_margin_available": l_margin_avail,
                    "previous_operating_margin_available": p_margin_avail,
                    "operating_margin_trend_available": trend_avail,
                    "profitability_available": l_margin_avail
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
                # Preserve existing sector mapping if missing
                if not rec.sector and v["sector"]: rec.sector = v["sector"]
                if not rec.industry and v["industry"]: rec.industry = v["industry"]
                if not rec.basic_industry and v["basic_industry"]: rec.basic_industry = v["basic_industry"]
                
                existing_calc = dict(rec.calculation_details) if rec.calculation_details else {}
                existing_calc["profitability"] = v["calculation_details"]["profitability"]
                # Merge warnings safely
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

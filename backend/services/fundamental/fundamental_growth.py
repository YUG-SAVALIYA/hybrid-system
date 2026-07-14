"""
FundamentalGrowthService

Calculates raw company growth metrics based on selected financial periods.
"""
from __future__ import annotations

import logging
import uuid
from sqlalchemy import text, cast
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert, JSONB

from models.discovery import CompanyFundamentalMetric
from services.fundamental.fundamental_period_selection import FundamentalPeriodSelectionService

logger = logging.getLogger(__name__)


def _get_np_transition(prev: float, latest: float) -> str:
    if prev > 0:
        return "STANDARD_GROWTH"
    if prev < 0:
        if latest > 0: return "LOSS_TO_PROFIT"
        if latest > prev: return "LOSS_NARROWED"  # covers latest == 0
        if latest < prev: return "LOSS_WIDENED"
        return "LOSS_UNCHANGED"
    # prev == 0
    if latest > 0: return "ZERO_BASE_TO_PROFIT"
    if latest < 0: return "ZERO_BASE_TO_LOSS"
    return "ZERO_BASE_UNCHANGED"


class FundamentalGrowthService:
    def __init__(self, source_session: Session, discovery_session: Session):
        self._src = source_session
        self._disc = discovery_session
        self._period_svc = FundamentalPeriodSelectionService(self._src)

    def calculate_growth(self, run_id: str) -> None:
        # Get period selections
        selections = self._period_svc.select_periods()
        
        if not selections:
            return

        # Collect overview_ids needing P&L fetch
        overview_ids = [s["overview_id"] for s in selections if s["overview_id"] and s["profit_loss"]["comparable"]]
        
        pl_data_map = {} # overview_id -> {period: {sales, net_profit}}
        if overview_ids:
            pl_query = text("""
                SELECT company_id, period, sales, net_profit
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
                    "net_profit": r.net_profit
                }

        # Need hierarchy from discovery to populate correctly?
        # The prompt says: "Persist only: Company identity and hierarchy, ... If the current schema is horizon-specific, preserve the existing schema convention rather than inventing another uniqueness rule."
        # Wait, the CompanyFundamentalMetric has run_id, source_company_id, symbol, sector, industry, basic_industry.
        # We can fetch the hierarchy from companies table in source DB to fill them initially.
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
            
            sales_growth_pct = None
            sales_growth_available = False
            np_growth_pct = None
            np_growth_available = False
            np_transition = None

            if pl_comp and overview_id and overview_id in pl_data_map:
                latest_data = pl_data_map[overview_id].get(latest_period, {})
                prev_data = pl_data_map[overview_id].get(prev_period, {})

                l_sales = latest_data.get("sales")
                p_sales = prev_data.get("sales")
                l_np = latest_data.get("net_profit")
                p_np = prev_data.get("net_profit")

                # Sales logic
                if l_sales is None: warnings.add("MISSING_LATEST_SALES")
                if p_sales is None: warnings.add("MISSING_PREVIOUS_SALES")
                
                if l_sales is not None and p_sales is not None:
                    if p_sales > 0:
                        sales_growth_pct = ((l_sales / p_sales) - 1.0) * 100.0
                        sales_growth_available = True
                    else:
                        warnings.add("INVALID_PREVIOUS_SALES_BASE")

                # Net Profit logic
                if l_np is None: warnings.add("MISSING_LATEST_NET_PROFIT")
                if p_np is None: warnings.add("MISSING_PREVIOUS_NET_PROFIT")

                if l_np is not None and p_np is not None:
                    np_transition = _get_np_transition(p_np, l_np)
                    if p_np > 0:
                        np_growth_pct = ((l_np / p_np) - 1.0) * 100.0
                        np_growth_available = True
                    else:
                        warnings.add("NON_STANDARD_NET_PROFIT_BASE")
            else:
                if not pl_comp:
                    warnings.add("INSUFFICIENT_PROFIT_LOSS_PERIODS")

            # Hierarchy
            hr = h_map.get(source_comp_id)
            sector = hr.sectore if hr else None
            industry = hr.industry if hr else None
            basic_industry = hr.categorized_industry if hr else None

            calc_details = {
                "growth": {
                    "latest_period": latest_period,
                    "previous_period": prev_period,
                    "sales_growth_available": sales_growth_available,
                    "sales_growth_pct": sales_growth_pct,
                    "net_profit_growth_available": np_growth_available,
                    "net_profit_growth_pct": np_growth_pct,
                    "net_profit_transition": np_transition,
                    "growth_available": sales_growth_available or np_growth_available
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

        # Fetch existing to do manual upsert since there is no UniqueConstraint on CompanyFundamentalMetric
        existing_records = self._disc.query(CompanyFundamentalMetric).filter_by(run_id=run_id).all()
        existing_map = {r.source_company_id: r for r in existing_records}

        for v in values_to_upsert:
            cid = v["source_company_id"]
            if cid in existing_map:
                rec = existing_map[cid]
                rec.sector = v["sector"]
                rec.industry = v["industry"]
                rec.basic_industry = v["basic_industry"]
                
                existing_calc = dict(rec.calculation_details) if rec.calculation_details else {}
                existing_calc["growth"] = v["calculation_details"]["growth"]
                existing_calc["warnings"] = v["calculation_details"]["warnings"]
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

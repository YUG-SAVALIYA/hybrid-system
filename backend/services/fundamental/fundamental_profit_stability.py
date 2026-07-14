"""
FundamentalProfitStabilityService

Calculates raw company profit-stability metrics based on historical net profit.
"""
from __future__ import annotations

import logging
import uuid
import math
from sqlalchemy import text
from sqlalchemy.orm import Session
import copy

import config
from models.discovery import CompanyFundamentalMetric
from services.fundamental.fundamental_period_selection import _classify_and_filter_periods, _days_between

logger = logging.getLogger(__name__)

MIN_PROFIT_STABILITY_PERIODS = getattr(config, 'MIN_PROFIT_STABILITY_PERIODS', 3)
MAX_PROFIT_STABILITY_PERIODS = getattr(config, 'MAX_PROFIT_STABILITY_PERIODS', 5)

def _get_sign(val: float) -> str:
    if val > 0: return "POSITIVE"
    if val < 0: return "NEGATIVE"
    return "ZERO"

class FundamentalProfitStabilityService:
    def __init__(self, source_session: Session, discovery_session: Session):
        self._src = source_session
        self._disc = discovery_session

    def calculate_profit_stability(self, run_id: str) -> None:
        query = text("""
            SELECT 
                c.id as source_company_id,
                c.share_symbol as symbol,
                co.id as overview_id,
                c.sectore as sector,
                c.industry as industry,
                c.categorized_industry as basic_industry
            FROM companies c
            LEFT JOIN company_overviews co ON c.share_symbol = co.share_symbol
        """)
        
        records = self._src.execute(query).fetchall()
        overview_ids = [r.overview_id for r in records if r.overview_id]
        
        pl_data_map = {}
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

        values_to_upsert = []

        for r in records:
            source_comp_id = r.source_company_id
            if not source_comp_id:
                continue
                
            overview_id = r.overview_id
            warnings = set()
            
            # 1-3. Find valid annual confirmed periods and sort
            valid_periods = []
            if overview_id and overview_id in pl_data_map:
                all_periods = list(pl_data_map[overview_id].keys())
                valid_periods = _classify_and_filter_periods(all_periods)
                # valid_periods is already sorted from newest to oldest by period_end
                
            selected_series = []
            
            # 4-9. Consecutive annual series
            if valid_periods:
                for i in range(len(valid_periods)):
                    if len(selected_series) >= MAX_PROFIT_STABILITY_PERIODS:
                        break
                        
                    p_dict = valid_periods[i]
                    orig_p = p_dict["original_period"]
                    pat = pl_data_map[overview_id].get(orig_p)
                    
                    # 7. Stop at missing net profit
                    if pat is None:
                        warnings.add("MISSING_NET_PROFIT_IN_STABILITY_SERIES")
                        break
                        
                    # 5-6. Check gap with previous added period (which is newer)
                    if len(selected_series) > 0:
                        newer_end = selected_series[-1]["_period_end"]
                        older_end = p_dict["period_end"]
                        gap = _days_between(newer_end, older_end)
                        if not (300 <= gap <= 430):
                            warnings.add("NON_CONSECUTIVE_ANNUAL_PERIODS")
                            break
                            
                    selected_series.append({
                        "period": orig_p,
                        "net_profit": pat,
                        "sign": _get_sign(pat),
                        "_period_end": p_dict["period_end"]  # keep for gap check, drop later
                    })

            # Calculate metrics if enough periods
            selected_count = len(selected_series)
            avail = selected_count >= MIN_PROFIT_STABILITY_PERIODS
            
            if not avail:
                warnings.add("INSUFFICIENT_CONSECUTIVE_PAT_PERIODS")
                
            pos_ratio = None
            loss_ratio = None
            zero_ratio = None
            latest_pos_streak = None
            sign_change_count = None
            valid_obs_count = None
            mean_growth = None
            volatility = None
            status = "UNAVAILABLE"
            vol_avail = False
            
            final_periods = []
            
            if selected_count > 0:
                pos_count = sum(1 for p in selected_series if p["sign"] == "POSITIVE")
                loss_count = sum(1 for p in selected_series if p["sign"] == "NEGATIVE")
                zero_count = sum(1 for p in selected_series if p["sign"] == "ZERO")
                
                pos_ratio = (pos_count / selected_count) * 100.0
                loss_ratio = (loss_count / selected_count) * 100.0
                zero_ratio = (zero_count / selected_count) * 100.0
                
                # latest positive streak
                streak = 0
                for p in selected_series:
                    if p["sign"] == "POSITIVE":
                        streak += 1
                    else:
                        break
                latest_pos_streak = streak
                
                # sign changes (chronological adjacent periods)
                # selected_series is newest to oldest. Reversing to chronological: oldest to newest
                chrono = list(reversed(selected_series))
                changes = 0
                for i in range(1, len(chrono)):
                    if chrono[i]["sign"] != chrono[i-1]["sign"]:
                        changes += 1
                sign_change_count = changes
                
                # PAT growth observations
                growth_rates = []
                for i in range(1, len(chrono)):
                    older_pat = chrono[i-1]["net_profit"]
                    newer_pat = chrono[i]["net_profit"]
                    if older_pat > 0:
                        g = ((newer_pat / older_pat) - 1.0) * 100.0
                        growth_rates.append(g)
                        
                valid_obs_count = len(growth_rates)
                if valid_obs_count >= 2:
                    mean_growth = sum(growth_rates) / valid_obs_count
                    variance = sum((g - mean_growth)**2 for g in growth_rates) / valid_obs_count
                    volatility = math.sqrt(variance)
                    vol_avail = True
                elif valid_obs_count == 1:
                    mean_growth = growth_rates[0]
                    warnings.add("INSUFFICIENT_VALID_PAT_GROWTH_OBSERVATIONS")
                else:
                    warnings.add("INSUFFICIENT_VALID_PAT_GROWTH_OBSERVATIONS")
                    
                # Status
                if avail:
                    if pos_ratio == 100.0:
                        status = "CONSISTENTLY_PROFITABLE"
                    elif 60.0 <= pos_ratio < 100.0:
                        status = "MOSTLY_PROFITABLE"
                    elif 0 < pos_ratio < 60.0:
                        status = "MIXED_PROFITABILITY"
                    elif pos_ratio == 0:
                        status = "CONSISTENTLY_NON_PROFITABLE"
                    
            # cleanup output periods
            for p in selected_series:
                final_periods.append({
                    "period": p["period"],
                    "net_profit": p["net_profit"],
                    "sign": p["sign"]
                })

            calc_details = {
                "earnings_quality": {
                    "profit_stability": {
                        "selected_period_count": selected_count,
                        "periods": final_periods,
                        "positive_pat_period_ratio": pos_ratio,
                        "loss_pat_period_ratio": loss_ratio,
                        "zero_pat_period_ratio": zero_ratio,
                        "latest_positive_pat_streak": latest_pos_streak,
                        "pat_sign_change_count": sign_change_count,
                        "valid_pat_growth_observation_count": valid_obs_count,
                        "mean_pat_growth_pct": mean_growth,
                        "pat_growth_volatility_pct": volatility,
                        "status": status,
                        "profit_stability_available": avail,
                        "pat_growth_volatility_available": vol_avail
                    }
                },
                "warnings": sorted(list(warnings))
            }

            values_to_upsert.append({
                "id": str(uuid.uuid4()),
                "run_id": run_id,
                "source_company_id": source_comp_id,
                "symbol": r.symbol,
                "sector": r.sector,
                "industry": r.industry,
                "basic_industry": r.basic_industry,
                "calculation_details": calc_details
            })

        if not values_to_upsert:
            return

        existing_records = self._disc.query(CompanyFundamentalMetric).filter_by(run_id=run_id).all()
        existing_map = {rec.source_company_id: rec for rec in existing_records}

        for v in values_to_upsert:
            cid = v["source_company_id"]
            if cid in existing_map:
                rec = existing_map[cid]
                if not rec.sector and v["sector"]: rec.sector = v["sector"]
                if not rec.industry and v["industry"]: rec.industry = v["industry"]
                if not rec.basic_industry and v["basic_industry"]: rec.basic_industry = v["basic_industry"]
                
                existing_calc = copy.deepcopy(rec.calculation_details) if rec.calculation_details else {}
                
                eq = existing_calc.get("earnings_quality", {})
                eq["profit_stability"] = v["calculation_details"]["earnings_quality"]["profit_stability"]
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

"""
FundamentalIndustryAggregationService

Calculates raw fundamental aggregation for industries.
"""
from __future__ import annotations

import logging
import copy
from typing import Dict, List, Any
from sqlalchemy.orm import Session

import config
from models.discovery import CompanyFundamentalMetric, GroupScore

logger = logging.getLogger(__name__)

MIN_INDUSTRY_FUNDAMENTAL_COMPANIES = getattr(config, 'MIN_INDUSTRY_FUNDAMENTAL_COMPANIES', 3)

def _is_finite(val: float | None) -> bool:
    if val is None: return False
    if isinstance(val, (int, float)):
        return val == val and val != float('inf') and val != float('-inf')
    return False

def _calculate_median(values: List[float]) -> float | None:
    if not values:
        return None
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    mid = n // 2
    if n % 2 == 1:
        return float(sorted_vals[mid])
    else:
        return float((sorted_vals[mid - 1] + sorted_vals[mid]) / 2.0)

def _aggregate_metric(values: List[float], applicable_count: int) -> Dict[str, Any]:
    valid_count = len(values)
    median = _calculate_median(values)
    coverage = None
    reason = None
    if applicable_count == 0:
        reason = "N_A_NO_STANDARD_DEBT_RULE_COMPANIES"
    else:
        coverage = round((valid_count / applicable_count) * 100.0, 2)
    
    return {
        "median": median,
        "valid_count": valid_count,
        "applicable_count": applicable_count,
        "coverage_pct": coverage,
        "reason": reason
    }

def _aggregate_distribution(values: List[str]) -> Dict[str, Any]:
    valid_status_count = len(values)
    counts = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
        
    percentages = {}
    if valid_status_count > 0:
        for k, v in counts.items():
            percentages[k] = round((v / valid_status_count) * 100.0, 2)
            
    return {
        "valid_status_count": valid_status_count,
        "counts": counts,
        "percentages": percentages
    }

class FundamentalIndustryAggregationService:
    def __init__(self, discovery_session: Session):
        self._disc = discovery_session

    def aggregate_industries(self, run_id: str) -> None:
        companies = self._disc.query(CompanyFundamentalMetric).filter_by(run_id=run_id).all()
        if not companies:
            return

        # Group by sector + industry
        industries_map: Dict[tuple, List[CompanyFundamentalMetric]] = {}
        for c in companies:
            if not c.sector or not c.industry:
                continue
            key = (c.sector, c.industry)
            industries_map.setdefault(key, []).append(c)

        # Existing group scores
        existing_groups = self._disc.query(GroupScore).filter_by(
            run_id=run_id, 
            entity_type="INDUSTRY", 
            parent_industry=""
        ).all()
        
        group_by_key = {(g.parent_sector, g.entity_name): g for g in existing_groups}

        for (sector_name, industry_name), mems in industries_map.items():
            constituent_count = len(mems)
            fundamental_score_available_count = 0
            fundamental_selection_eligible_count = 0
            standard_debt_rule_applicable_count = 0
            standard_debt_rule_not_applicable_count = 0

            # Raw values collectors
            raw_metrics = {
                "sales_growth_pct": [],
                "net_profit_growth_pct": [],
                "latest_operating_margin_pct": [],
                "operating_margin_change_pp": [],
                "debt_to_equity": [],
                "borrowing_change_pct": [],
                "latest_ocf_to_pat": [],
                "ocf_to_pat_change": [],
                "positive_pat_period_ratio": [],
                "pat_growth_volatility_pct": []
            }
            
            transitions = {
                "net_profit": [],
                "borrowing": [],
                "cash_conversion": []
            }

            for m in mems:
                calc = m.calculation_details or {}
                
                # Check fundamental score available
                if m.final_fundamental_score is not None and _is_finite(m.final_fundamental_score):
                    fundamental_score_available_count += 1
                if m.fundamental_eligible_for_selection:
                    fundamental_selection_eligible_count += 1
                    
                fs_dict = calc.get("financial_strength", {})
                std_debt_applicable = fs_dict.get("standard_debt_rule_applicable", True)
                if std_debt_applicable:
                    standard_debt_rule_applicable_count += 1
                else:
                    standard_debt_rule_not_applicable_count += 1

                # Growth
                growth = calc.get("growth", {})
                if growth.get("sales_growth_pct_available") and _is_finite(growth.get("sales_growth_pct")):
                    raw_metrics["sales_growth_pct"].append(growth["sales_growth_pct"])
                if growth.get("net_profit_growth_pct_available") and _is_finite(growth.get("net_profit_growth_pct")):
                    raw_metrics["net_profit_growth_pct"].append(growth["net_profit_growth_pct"])
                
                np_trans = growth.get("net_profit_transition")
                if np_trans:
                    transitions["net_profit"].append(np_trans)

                # Profitability
                prof = calc.get("profitability", {})
                if prof.get("latest_operating_margin_pct_available") and _is_finite(prof.get("latest_operating_margin_pct")):
                    raw_metrics["latest_operating_margin_pct"].append(prof["latest_operating_margin_pct"])
                if prof.get("operating_margin_change_pp_available") and _is_finite(prof.get("operating_margin_change_pp")):
                    raw_metrics["operating_margin_change_pp"].append(prof["operating_margin_change_pp"])

                # Financial Strength
                if std_debt_applicable:
                    if fs_dict.get("debt_to_equity_available") and _is_finite(fs_dict.get("debt_to_equity")):
                        raw_metrics["debt_to_equity"].append(fs_dict["debt_to_equity"])
                    if fs_dict.get("borrowing_trend_available") and _is_finite(fs_dict.get("borrowing_change_pct")):
                        raw_metrics["borrowing_change_pct"].append(fs_dict["borrowing_change_pct"])
                    
                    b_trans = fs_dict.get("borrowing_transition")
                    if b_trans:
                        transitions["borrowing"].append(b_trans)

                # Earnings Quality
                eq = calc.get("earnings_quality", {})
                cc = eq.get("cash_conversion", {})
                if cc.get("latest_ocf_to_pat_available") and _is_finite(cc.get("latest_ocf_to_pat")):
                    raw_metrics["latest_ocf_to_pat"].append(cc["latest_ocf_to_pat"])
                if cc.get("ocf_to_pat_change_available") and _is_finite(cc.get("ocf_to_pat_change")):
                    raw_metrics["ocf_to_pat_change"].append(cc["ocf_to_pat_change"])
                
                cc_status = cc.get("latest_cash_conversion_status")
                if cc_status:
                    transitions["cash_conversion"].append(cc_status)
                    
                ps = eq.get("profit_stability", {})
                if ps.get("profit_stability_available") and _is_finite(ps.get("positive_pat_period_ratio")):
                    raw_metrics["positive_pat_period_ratio"].append(ps["positive_pat_period_ratio"])
                if ps.get("pat_growth_volatility_available") and _is_finite(ps.get("pat_growth_volatility_pct")):
                    raw_metrics["pat_growth_volatility_pct"].append(ps["pat_growth_volatility_pct"])

            # Aggregate Numeric
            aggregated_metrics = {}
            for k in ["sales_growth_pct", "net_profit_growth_pct", "latest_operating_margin_pct", 
                      "operating_margin_change_pp", "latest_ocf_to_pat", "ocf_to_pat_change", 
                      "positive_pat_period_ratio", "pat_growth_volatility_pct"]:
                aggregated_metrics[k] = _aggregate_metric(raw_metrics[k], constituent_count)
                
            for k in ["debt_to_equity", "borrowing_change_pct"]:
                aggregated_metrics[k] = _aggregate_metric(raw_metrics[k], standard_debt_rule_applicable_count)
                
            # Aggregate Distributions
            aggregated_transitions = {
                "net_profit": _aggregate_distribution(transitions["net_profit"]),
                "borrowing": _aggregate_distribution(transitions["borrowing"]),
                "cash_conversion": _aggregate_distribution(transitions["cash_conversion"])
            }

            # Group Score persistence
            group_score = group_by_key.get((sector_name, industry_name))
            if not group_score:
                import uuid
                group_score = GroupScore(
                    id=str(uuid.uuid4()),
                    run_id=run_id,
                    entity_type="INDUSTRY",
                    entity_name=industry_name,
                    parent_sector=sector_name,
                    parent_industry="",
                    horizon="1Y"
                )
                self._disc.add(group_score)

            # Warnings
            warnings_set = set(group_score.warnings or [])
            warnings_set.discard("INSUFFICIENT_FUNDAMENTAL_CONSTITUENTS")
            
            if fundamental_score_available_count < MIN_INDUSTRY_FUNDAMENTAL_COMPANIES:
                warnings_set.add("INSUFFICIENT_FUNDAMENTAL_CONSTITUENTS")

            calc_details = copy.deepcopy(group_score.calculation_details or {})
            if "fundamental" not in calc_details:
                calc_details["fundamental"] = {}
                
            calc_details["fundamental"]["raw_aggregation"] = {
                "constituent_count": constituent_count,
                "fundamental_score_available_count": fundamental_score_available_count,
                "fundamental_selection_eligible_count": fundamental_selection_eligible_count,
                "standard_debt_rule_applicable_count": standard_debt_rule_applicable_count,
                "standard_debt_rule_not_applicable_count": standard_debt_rule_not_applicable_count,
                "metrics": aggregated_metrics,
                "transitions": aggregated_transitions
            }

            group_score.constituent_count = constituent_count
            
            warn_list = list(warnings_set)
            warn_list.sort()
            group_score.warnings = warn_list
            group_score.calculation_details = calc_details

        self._disc.commit()

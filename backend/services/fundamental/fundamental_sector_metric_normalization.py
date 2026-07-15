"""
FundamentalSectorMetricNormalizationService

Calculates percentile normalization of raw sector fundamental medians.
"""
from __future__ import annotations

import logging
import copy
from typing import Dict, List, Any
from sqlalchemy.orm import Session
from collections import defaultdict

import config
from models.discovery import GroupScore

logger = logging.getLogger(__name__)

MIN_SECTOR_FUNDAMENTAL_COMPANIES = getattr(config, 'MIN_SECTOR_FUNDAMENTAL_COMPANIES', 5)
MIN_GROUP_FUNDAMENTAL_METRIC_OBSERVATIONS = getattr(config, 'MIN_GROUP_FUNDAMENTAL_METRIC_OBSERVATIONS', 3)
MIN_GROUP_FUNDAMENTAL_METRIC_COVERAGE = getattr(config, 'MIN_GROUP_FUNDAMENTAL_METRIC_COVERAGE', 60.0)

HIGHER_IS_BETTER = [
    "sales_growth_pct",
    "net_profit_growth_pct",
    "latest_operating_margin_pct",
    "operating_margin_change_pp",
    "latest_ocf_to_pat",
    "ocf_to_pat_change",
    "positive_pat_period_ratio"
]

LOWER_IS_BETTER = [
    "debt_to_equity",
    "borrowing_change_pct",
    "pat_growth_volatility_pct"
]

def _is_finite(val: float | None) -> bool:
    if val is None: return False
    if isinstance(val, (int, float)):
        return val == val and val != float('inf') and val != float('-inf')
    return False

def _rank_values(values: List[float], higher_is_better: bool) -> Dict[float, float]:
    """Calculate average rank for ties. Returns dict of val -> rank (1-indexed)."""
    if not values:
        return {}
    
    sorted_unique = sorted(list(set(values)), reverse=not higher_is_better)
    val_to_indices = defaultdict(list)
    
    sorted_all = sorted(values, reverse=not higher_is_better)
    for i, v in enumerate(sorted_all):
        val_to_indices[v].append(i + 1)
        
    val_to_rank = {}
    for v, indices in val_to_indices.items():
        val_to_rank[v] = sum(indices) / len(indices)
        
    return val_to_rank

class FundamentalSectorMetricNormalizationService:
    def __init__(self, discovery_session: Session):
        self._disc = discovery_session

    def normalize_metrics(
        self,
        run_id: str,
        horizon: str | None = None,
        sectors: list[str] | None = None,
    ) -> None:
        query = self._disc.query(GroupScore).filter_by(
            run_id=run_id,
            entity_type="SECTOR",
        )
        if horizon is not None:
            query = query.filter_by(horizon=horizon)
        if sectors is not None:
            query = query.filter(GroupScore.entity_name.in_(sectors))
        sectors = query.all()
        if not sectors:
            return

        all_metrics = HIGHER_IS_BETTER + LOWER_IS_BETTER
        
        # Step 1: Collect eligible values for each metric across all sectors
        metric_eligible_values = defaultdict(list)
        sector_eligibility_cache = defaultdict(dict)
        
        for sector in sectors:
            calc = sector.calculation_details or {}
            raw_agg = calc.get("fundamental", {}).get("raw_aggregation", {})
            metrics = raw_agg.get("metrics", {})
            const_count = raw_agg.get("constituent_count", 0)
            
            for m_name in all_metrics:
                m_data = metrics.get(m_name, {})
                
                # Check applicability
                reason = m_data.get("reason")
                if reason == "N_A_NO_STANDARD_DEBT_RULE_COMPANIES":
                    sector_eligibility_cache[sector.id][m_name] = {
                        "applicable": False,
                        "eligible": False,
                        "reason": reason
                    }
                    continue
                    
                applicable = True
                eligible = False
                eligibility_reason = None
                
                median = m_data.get("median")
                app_cnt = m_data.get("applicable_count", 0)
                val_cnt = m_data.get("valid_count", 0)
                cov_pct = m_data.get("coverage_pct")
                
                if const_count < MIN_SECTOR_FUNDAMENTAL_COMPANIES:
                    eligibility_reason = "INSUFFICIENT_CONSTITUENTS"
                elif not _is_finite(median):
                    eligibility_reason = "RAW_MEDIAN_UNAVAILABLE"
                elif app_cnt == 0:
                    eligibility_reason = "RAW_MEDIAN_UNAVAILABLE" 
                elif val_cnt < MIN_GROUP_FUNDAMENTAL_METRIC_OBSERVATIONS:
                    eligibility_reason = "INSUFFICIENT_METRIC_OBSERVATIONS"
                elif cov_pct is None or cov_pct < MIN_GROUP_FUNDAMENTAL_METRIC_COVERAGE:
                    eligibility_reason = "LOW_METRIC_COVERAGE"
                else:
                    eligible = True
                    
                sector_eligibility_cache[sector.id][m_name] = {
                    "applicable": applicable,
                    "eligible": eligible,
                    "reason": eligibility_reason,
                    "median": median
                }
                
                if eligible:
                    metric_eligible_values[m_name].append(median)

        # Step 2: Rank eligible values
        metric_ranks = {}
        for m_name in all_metrics:
            is_higher = m_name in HIGHER_IS_BETTER
            vals = metric_eligible_values[m_name]
            metric_ranks[m_name] = _rank_values(vals, is_higher)
            
        # Step 3: Populate sector scores
        for sector in sectors:
            calc_details = copy.deepcopy(sector.calculation_details or {})
            if "fundamental" not in calc_details:
                calc_details["fundamental"] = {}
                
            norm_metrics = {}
            applicable_metric_count = 0
            scored_metric_count = 0
            
            for m_name in all_metrics:
                elig_info = sector_eligibility_cache[sector.id].get(m_name, {})
                
                applicable = elig_info.get("applicable", True)
                eligible = elig_info.get("eligible", False)
                reason = elig_info.get("reason")
                median = elig_info.get("median")
                
                direction = "HIGHER_IS_BETTER" if m_name in HIGHER_IS_BETTER else "LOWER_IS_BETTER"
                
                comparison_set_size = len(metric_eligible_values[m_name]) if eligible else 0
                
                score = None
                rank = None
                
                if not applicable:
                    pass
                else:
                    applicable_metric_count += 1
                    if eligible:
                        if comparison_set_size == 1:
                            score = 50.0
                            rank = 1.0
                            reason = "SINGLE_SECTOR_METRIC_COMPARISON"
                        elif comparison_set_size > 1:
                            rank = metric_ranks[m_name][median]
                            score = ((rank - 1) / (comparison_set_size - 1)) * 100.0
                        else:
                            # Should not reach here if eligible is true, but just in case
                            reason = "NO_ELIGIBLE_COMPARISON_SET"
                    else:
                        # Wait, what if it's not eligible but there's no comparison set? 
                        # Reason is already set above
                        pass
                
                if score is not None:
                    scored_metric_count += 1
                
                # Format output score
                if score is not None:
                    score = round(score, 2)
                    
                norm_metrics[m_name] = {
                    "raw_median": median,
                    "direction": direction,
                    "applicable": applicable,
                    "eligible": eligible,
                    "comparison_set_size": comparison_set_size,
                    "rank": rank,
                    "score": score,
                    "reason": reason
                }

            coverage_pct = 0.0
            if applicable_metric_count > 0:
                coverage_pct = round((scored_metric_count / applicable_metric_count) * 100.0, 2)

            calc_details["fundamental"]["metric_normalization"] = {
                "metrics": norm_metrics,
                "applicable_metric_count": applicable_metric_count,
                "scored_metric_count": scored_metric_count,
                "coverage_pct": coverage_pct
            }
            
            sector.calculation_details = calc_details
            
        self._disc.commit()

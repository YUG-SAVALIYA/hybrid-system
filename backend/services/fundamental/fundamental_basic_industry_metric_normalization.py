"""
FundamentalBasicIndustryMetricNormalizationService

Calculates percentile normalization of raw basic-industry fundamental medians
within sibling comparison boundaries (same parent sector and industry).
"""
from __future__ import annotations

import logging
import copy
from typing import Dict, List, Any
from sqlalchemy.orm import Session

import config
from models.discovery import GroupScore

logger = logging.getLogger(__name__)

MIN_BASIC_INDUSTRY_FUNDAMENTAL_COMPANIES = getattr(config, 'MIN_BASIC_INDUSTRY_FUNDAMENTAL_COMPANIES', 2)
MIN_GROUP_FUNDAMENTAL_METRIC_OBSERVATIONS = getattr(config, 'MIN_GROUP_FUNDAMENTAL_METRIC_OBSERVATIONS', 3)
MIN_GROUP_FUNDAMENTAL_METRIC_COVERAGE = getattr(config, 'MIN_GROUP_FUNDAMENTAL_METRIC_COVERAGE', 60.0)

HIGHER_IS_BETTER = {
    "sales_growth_pct",
    "net_profit_growth_pct",
    "latest_operating_margin_pct",
    "operating_margin_change_pp",
    "latest_ocf_to_pat",
    "ocf_to_pat_change",
    "positive_pat_period_ratio"
}

LOWER_IS_BETTER = {
    "debt_to_equity",
    "borrowing_change_pct",
    "pat_growth_volatility_pct"
}

def _is_finite(val: float | None) -> bool:
    if val is None: return False
    if isinstance(val, (int, float)):
        return val == val and val != float('inf') and val != float('-inf')
    return False

def _score_percentiles(values_map: Dict[str, float], direction: str) -> Dict[str, float]:
    if not values_map:
        return {}
        
    n = len(values_map)
    if n == 1:
        return {k: 50.0 for k in values_map}
        
    reverse = (direction == "LOWER_IS_BETTER")
    
    sorted_items = sorted(values_map.items(), key=lambda x: x[1], reverse=reverse)
    
    ranks = {}
    i = 0
    while i < n:
        start_idx = i
        val = sorted_items[i][1]
        
        while i < n and sorted_items[i][1] == val:
            i += 1
            
        end_idx = i - 1
        avg_rank = (start_idx + end_idx) / 2.0 + 1.0
        score = ((avg_rank - 1.0) / (n - 1.0)) * 100.0
        score = round(score, 2)
        
        for j in range(start_idx, i):
            ranks[sorted_items[j][0]] = score
            
    return ranks

class FundamentalBasicIndustryMetricNormalizationService:
    def __init__(self, discovery_session: Session):
        self._disc = discovery_session

    def normalize_basic_industry_metrics(
        self,
        run_id: str,
        horizon: str | None = None,
        parent_sector: str | None = None,
        parent_industry: str | None = None,
    ) -> None:
        query = self._disc.query(GroupScore).filter_by(
            run_id=run_id,
            entity_type="BASIC_INDUSTRY",
        )
        if horizon is not None:
            query = query.filter_by(horizon=horizon)
        if parent_sector is not None:
            query = query.filter(GroupScore.parent_sector == parent_sector)
        if parent_industry is not None:
            query = query.filter(GroupScore.parent_industry == parent_industry)
        bi_groups = query.all()
        
        if not bi_groups:
            return
            
        sibling_map = {}
        for bi in bi_groups:
            if bi.parent_sector and bi.parent_industry:
                sibling_map.setdefault((bi.parent_sector, bi.parent_industry), []).append(bi)
                
        for (parent_sec, parent_ind), siblings in sibling_map.items():
            metrics_to_score = {}
            for metric in HIGHER_IS_BETTER | LOWER_IS_BETTER:
                metrics_to_score[metric] = {}
                
            for bi in siblings:
                calc = bi.calculation_details or {}
                raw_agg = calc.get("fundamental", {}).get("raw_aggregation", {})
                
                avail_count = raw_agg.get("fundamental_score_available_count", 0)
                metrics = raw_agg.get("metrics", {})
                
                for m_name in HIGHER_IS_BETTER | LOWER_IS_BETTER:
                    m_data = metrics.get(m_name, {})
                    val = m_data.get("median")
                    valid_cnt = m_data.get("valid_count", 0)
                    app_cnt = m_data.get("applicable_count", 0)
                    cov = m_data.get("coverage_pct", 0.0)
                    reason = m_data.get("reason")
                    
                    applicable = True
                    eligible = False
                    el_reason = None
                    
                    if m_name in ["debt_to_equity", "borrowing_change_pct"] and reason == "N_A_NO_STANDARD_DEBT_RULE_COMPANIES":
                        applicable = False
                        el_reason = "N_A_NO_STANDARD_DEBT_RULE_COMPANIES"
                    else:
                        if avail_count < MIN_BASIC_INDUSTRY_FUNDAMENTAL_COMPANIES:
                            el_reason = "INSUFFICIENT_CONSTITUENTS"
                        elif not _is_finite(val):
                            el_reason = "RAW_MEDIAN_UNAVAILABLE"
                        elif app_cnt == 0 or valid_cnt < MIN_GROUP_FUNDAMENTAL_METRIC_OBSERVATIONS:
                            el_reason = "INSUFFICIENT_METRIC_OBSERVATIONS"
                        elif cov is None or cov < MIN_GROUP_FUNDAMENTAL_METRIC_COVERAGE:
                            el_reason = "LOW_METRIC_COVERAGE"
                        else:
                            eligible = True
                            
                    metrics_to_score[m_name][bi.id] = {
                        "val": val,
                        "applicable": applicable,
                        "eligible": eligible,
                        "reason": el_reason
                    }
                    
            scores_map = {}
            for m_name, bi_data in metrics_to_score.items():
                direction = "HIGHER_IS_BETTER" if m_name in HIGHER_IS_BETTER else "LOWER_IS_BETTER"
                
                eligible_vals = {bi_id: d["val"] for bi_id, d in bi_data.items() if d["eligible"]}
                scores = _score_percentiles(eligible_vals, direction)
                
                for bi_id, d in bi_data.items():
                    scores_map.setdefault(bi_id, {})[m_name] = {
                        "raw_median": d["val"],
                        "direction": direction,
                        "applicable": d["applicable"],
                        "eligible": d["eligible"],
                        "comparison_set_size": len(eligible_vals) if d["eligible"] else 0,
                        "rank": None,
                        "score": None,
                        "reason": d["reason"]
                    }
                    
                    if d["eligible"]:
                        if len(eligible_vals) == 1:
                            scores_map[bi_id][m_name]["score"] = 50.0
                            scores_map[bi_id][m_name]["reason"] = "SINGLE_BASIC_INDUSTRY_METRIC_COMPARISON"
                        else:
                            scores_map[bi_id][m_name]["score"] = scores[bi_id]
                            
            for bi in siblings:
                bi_scores = scores_map.get(bi.id, {})
                
                app_cnt = 0
                scored_cnt = 0
                for m_name, s_data in bi_scores.items():
                    if s_data["applicable"]:
                        app_cnt += 1
                    if s_data["score"] is not None:
                        scored_cnt += 1
                        
                cov_pct = None
                if app_cnt > 0:
                    cov_pct = round((scored_cnt / app_cnt) * 100.0, 2)
                    
                calc = copy.deepcopy(bi.calculation_details or {})
                if "fundamental" not in calc:
                    calc["fundamental"] = {}
                    
                calc["fundamental"]["metric_normalization"] = {
                    "comparison_boundary": {
                        "parent_sector": parent_sec,
                        "parent_industry": parent_ind
                    },
                    "metrics": bi_scores,
                    "applicable_metric_count": app_cnt,
                    "scored_metric_count": scored_cnt,
                    "coverage_pct": cov_pct
                }
                
                bi.calculation_details = calc
                
        self._disc.commit()

"""
FundamentalIndustryScoreService

Calculates the final deterministic industry fundamental score based on pillar scores.
"""
from __future__ import annotations

import logging
import copy
from typing import Dict, Any, List
from sqlalchemy.orm import Session

import config
from models.discovery import GroupScore

logger = logging.getLogger(__name__)

MIN_GROUP_FUNDAMENTAL_COVERAGE = getattr(config, 'MIN_GROUP_FUNDAMENTAL_COVERAGE', 75.0)
MIN_INDUSTRY_FUNDAMENTAL_COMPANIES = getattr(config, 'MIN_INDUSTRY_FUNDAMENTAL_COMPANIES', 3)

def _is_finite(val: float | None) -> bool:
    if val is None: return False
    if isinstance(val, (int, float)):
        return val == val and val != float('inf') and val != float('-inf')
    return False

def _get_status(score: float | None) -> str:
    if score is None:
        return "UNAVAILABLE"
    if score >= 80.0:
        return "VERY_STRONG"
    if score >= 65.0:
        return "STRONG"
    if score >= 50.0:
        return "NEUTRAL"
    if score >= 35.0:
        return "WEAK"
    return "VERY_WEAK"

class FundamentalIndustryScoreService:
    def __init__(self, discovery_session: Session):
        self._disc = discovery_session

    def calculate_industry_scores(self, run_id: str) -> None:
        industries = self._disc.query(GroupScore).filter_by(
            run_id=run_id, 
            entity_type="INDUSTRY",
            parent_industry=""
        ).all()
        
        if not industries:
            return

        for ind in industries:
            calc = copy.deepcopy(ind.calculation_details or {})
            fund = calc.get("fundamental", {})
            pillars = fund.get("pillar_scores", {})
            raw_agg = fund.get("raw_aggregation", {})
            
            avail_cnt = raw_agg.get("fundamental_score_available_count", 0)
            
            warnings_set = set(ind.warnings or [])
            warnings_set.discard("INDUSTRY_FUNDAMENTAL_SCORE_PARTIAL")
            warnings_set.discard("INDUSTRY_FUNDAMENTAL_LOW_COVERAGE")
            warnings_set.discard("INDUSTRY_FUNDAMENTAL_SCORE_UNAVAILABLE")
            warnings_set.discard("INSUFFICIENT_FUNDAMENTAL_CONSTITUENTS")
            
            p_config = {
                "growth": {"weight": 25.0, "applicable": True},
                "profitability": {"weight": 25.0, "applicable": True},
                "financial_strength": {"weight": 25.0, "applicable": True},
                "earnings_quality": {"weight": 25.0, "applicable": True}
            }
            
            fs_data = pillars.get("financial_strength", {})
            if not fs_data.get("applicable", True):
                p_config["financial_strength"]["applicable"] = False
                
            applicable_w = 0.0
            available_w = 0.0
            weighted_sum = 0.0
            
            pillar_res = {}
            for p_name, p_conf in p_config.items():
                w = p_conf["weight"]
                app = p_conf["applicable"]
                p_data = pillars.get(p_name, {})
                sc = p_data.get("score")
                
                avail = False
                contribution = None
                reason = p_data.get("reason")
                
                if app:
                    applicable_w += w
                    if _is_finite(sc) and p_data.get("status") not in ("UNAVAILABLE", "N_A"):
                        avail = True
                        available_w += w
                        contribution = sc * w
                        weighted_sum += contribution
                        
                pillar_res[p_name] = {
                    "configured_weight": w,
                    "applicable": app,
                    "available": avail,
                    "score": sc if avail else None,
                    "weighted_contribution": contribution,
                    "reason": reason
                }

            cov_pct = 0.0
            if applicable_w > 0:
                cov_pct = round((available_w / applicable_w) * 100.0, 2)
                
            final_score = None
            if available_w > 0:
                final_score = round(weighted_sum / available_w, 2)
                
            status = _get_status(final_score)
            
            eligible = False
            if final_score is not None and cov_pct >= MIN_GROUP_FUNDAMENTAL_COVERAGE and avail_cnt >= MIN_INDUSTRY_FUNDAMENTAL_COMPANIES:
                eligible = True
                
            if available_w == 0:
                warnings_set.add("INDUSTRY_FUNDAMENTAL_SCORE_UNAVAILABLE")
            elif available_w < applicable_w:
                warnings_set.add("INDUSTRY_FUNDAMENTAL_SCORE_PARTIAL")
                
            if cov_pct < MIN_GROUP_FUNDAMENTAL_COVERAGE:
                warnings_set.add("INDUSTRY_FUNDAMENTAL_LOW_COVERAGE")
                
            if avail_cnt < MIN_INDUSTRY_FUNDAMENTAL_COMPANIES:
                warnings_set.add("INSUFFICIENT_FUNDAMENTAL_CONSTITUENTS")
                
            if "fundamental" not in calc:
                calc["fundamental"] = {}
                
            calc["fundamental"]["final_score"] = {
                "pillars": pillar_res,
                "applicable_weight": applicable_w,
                "available_weight": available_w,
                "coverage_pct": cov_pct,
                "score": final_score,
                "status": status,
                "fundamental_score_available_count": avail_cnt,
                "minimum_constituents_required": MIN_INDUSTRY_FUNDAMENTAL_COMPANIES,
                "eligible_for_selection": eligible
            }
            
            ind.fundamental_score = final_score
            
            w_list = list(warnings_set)
            w_list.sort()
            ind.warnings = w_list
            ind.calculation_details = calc
            
        self._disc.commit()

"""
FundamentalSectorScoreService

Calculates the final deterministic sector fundamental score.
"""
from __future__ import annotations

import logging
import copy
from typing import Dict, Any
from sqlalchemy.orm import Session

import config
from models.discovery import GroupScore

logger = logging.getLogger(__name__)

MIN_GROUP_FUNDAMENTAL_COVERAGE = getattr(config, 'MIN_GROUP_FUNDAMENTAL_COVERAGE', 75.0)
MIN_SECTOR_FUNDAMENTAL_COMPANIES = getattr(config, 'MIN_SECTOR_FUNDAMENTAL_COMPANIES', 5)

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

class FundamentalSectorScoreService:
    def __init__(self, discovery_session: Session):
        self._disc = discovery_session

    def calculate_final_scores(self, run_id: str) -> None:
        sectors = self._disc.query(GroupScore).filter_by(run_id=run_id, entity_type="SECTOR").all()
        if not sectors:
            return

        for sector in sectors:
            calc = copy.deepcopy(sector.calculation_details or {})
            fund = calc.get("fundamental", {})
            raw_agg = fund.get("raw_aggregation", {})
            pillar_scores = fund.get("pillar_scores", {})
            
            fund_avail_cnt = raw_agg.get("fundamental_score_available_count", 0)
            
            total_app_weight = 0.0
            total_avail_weight = 0.0
            weighted_sum = 0.0
            
            out_pillars = {}
            
            for p_key, weight in [
                ("growth", 25.0),
                ("profitability", 25.0),
                ("financial_strength", 25.0),
                ("earnings_quality", 25.0)
            ]:
                p_data = pillar_scores.get(p_key, {})
                applicable = p_data.get("applicable", True)
                p_score = p_data.get("score")
                p_status = p_data.get("status")
                p_reason = p_data.get("reason")
                
                available = False
                w_contrib = None
                
                if applicable:
                    total_app_weight += weight
                    if _is_finite(p_score) and p_status not in ("UNAVAILABLE", "N_A"):
                        available = True
                        total_avail_weight += weight
                        w_contrib = p_score * weight
                        weighted_sum += w_contrib
                        
                out_pillars[p_key] = {
                    "configured_weight": weight,
                    "applicable": applicable,
                    "available": available,
                    "score": p_score if available else None,
                    "weighted_contribution": w_contrib,
                    "reason": p_reason
                }
                
            final_score = None
            if total_avail_weight > 0:
                final_score = round(weighted_sum / total_avail_weight, 2)
                
            coverage_pct = 0.0
            if total_app_weight > 0:
                coverage_pct = round((total_avail_weight / total_app_weight) * 100.0, 2)
                
            status = _get_status(final_score)
            
            eligible = False
            if final_score is not None and coverage_pct >= MIN_GROUP_FUNDAMENTAL_COVERAGE and fund_avail_cnt >= MIN_SECTOR_FUNDAMENTAL_COMPANIES:
                eligible = True
                
            warnings_set = set(sector.warnings or [])
            warnings_set.discard("SECTOR_FUNDAMENTAL_SCORE_PARTIAL")
            warnings_set.discard("SECTOR_FUNDAMENTAL_LOW_COVERAGE")
            warnings_set.discard("SECTOR_FUNDAMENTAL_SCORE_UNAVAILABLE")
            
            if total_avail_weight == 0:
                warnings_set.add("SECTOR_FUNDAMENTAL_SCORE_UNAVAILABLE")
            else:
                if total_avail_weight < total_app_weight:
                    warnings_set.add("SECTOR_FUNDAMENTAL_SCORE_PARTIAL")
                if coverage_pct < MIN_GROUP_FUNDAMENTAL_COVERAGE:
                    warnings_set.add("SECTOR_FUNDAMENTAL_LOW_COVERAGE")
                    
            # Ensure constituent warning matches 
            if fund_avail_cnt < MIN_SECTOR_FUNDAMENTAL_COMPANIES:
                warnings_set.add("INSUFFICIENT_FUNDAMENTAL_CONSTITUENTS")
                
            if "fundamental" not in calc:
                calc["fundamental"] = {}
                
            calc["fundamental"]["final_score"] = {
                "pillars": out_pillars,
                "applicable_weight": total_app_weight,
                "available_weight": total_avail_weight,
                "coverage_pct": coverage_pct,
                "score": final_score,
                "status": status,
                "fundamental_score_available_count": fund_avail_cnt,
                "minimum_constituents_required": MIN_SECTOR_FUNDAMENTAL_COMPANIES,
                "eligible_for_selection": eligible
            }
            
            sector.fundamental_score = final_score
            warn_list = list(warnings_set)
            warn_list.sort()
            sector.warnings = warn_list
            sector.calculation_details = calc
            
        self._disc.commit()

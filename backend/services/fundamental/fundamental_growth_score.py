"""
FundamentalGrowthScoreService

Calculates fundamental growth scores based on peer-relative comparisons and deterministic transitions.
"""
from __future__ import annotations

import logging
import copy
from sqlalchemy.orm import Session

import config
from models.discovery import CompanyFundamentalMetric

logger = logging.getLogger(__name__)

GROWTH_PEER_FULL_SCALE_DELTA_PP = getattr(config, 'GROWTH_PEER_FULL_SCALE_DELTA_PP', 20.0)

TRANSITION_SCORES = {
    "LOSS_TO_PROFIT": 90,
    "LOSS_NARROWED": 65,
    "LOSS_UNCHANGED": 35,
    "LOSS_WIDENED": 10,
    "ZERO_BASE_TO_PROFIT": 85,
    "ZERO_BASE_TO_LOSS": 10,
    "ZERO_BASE_UNCHANGED": 30
}

def _calculate_relative_score(company_value: float, peer_median: float) -> float:
    delta = company_value - peer_median
    score = 50.0 + (delta / GROWTH_PEER_FULL_SCALE_DELTA_PP) * 50.0
    return max(0.0, min(100.0, score))

def _get_status(score: float) -> str:
    if score >= 80.0: return "VERY_STRONG"
    if score >= 65.0: return "STRONG"
    if score >= 50.0: return "NEUTRAL"
    if score >= 35.0: return "WEAK"
    return "VERY_WEAK"

def _is_finite(val: float | None) -> bool:
    if val is None: return False
    if isinstance(val, (int, float)):
        return val == val and val != float('inf') and val != float('-inf')
    return False

class FundamentalGrowthScoreService:
    def __init__(self, discovery_session: Session):
        self._disc = discovery_session

    def score_growth(self, run_id: str) -> None:
        records = self._disc.query(CompanyFundamentalMetric).filter_by(run_id=run_id).all()
        if not records:
            return

        for rec in records:
            calc_details = rec.calculation_details or {}
            growth = calc_details.get("growth", {})
            peer_benchmarks = calc_details.get("peer_benchmarks", {})
            pb_metrics = peer_benchmarks.get("metrics", {})
            warnings_set = set(calc_details.get("warnings", []))
            
            # 1. Sales Growth Score
            sales_score_dict = {
                "company_value": None,
                "peer_median": None,
                "peer_delta_pp": None,
                "score": None,
                "available": False
            }
            sales_avail = growth.get("sales_growth_available", False)
            sales_val = growth.get("sales_growth_pct")
            sales_pb = pb_metrics.get("sales_growth_pct", {})
            sales_peer_avail = sales_pb.get("available", False)
            sales_peer_median = sales_pb.get("peer_median")
            
            if sales_avail and _is_finite(sales_val):
                if sales_peer_avail and _is_finite(sales_peer_median):
                    sales_score_dict["company_value"] = sales_val
                    sales_score_dict["peer_median"] = sales_peer_median
                    sales_score_dict["peer_delta_pp"] = sales_val - sales_peer_median
                    sales_score_dict["score"] = _calculate_relative_score(sales_val, sales_peer_median)
                    sales_score_dict["available"] = True
                else:
                    warnings_set.add("SALES_PEER_BASELINE_UNAVAILABLE")

            # 2. Net Profit Growth Score
            np_score_dict = {
                "company_value": None,
                "peer_median": None,
                "transition": None,
                "score_source": "UNAVAILABLE",
                "score": None,
                "available": False
            }
            np_avail = growth.get("net_profit_growth_available", False)
            np_val = growth.get("net_profit_growth_pct")
            np_pb = pb_metrics.get("net_profit_growth_pct", {})
            np_peer_avail = np_pb.get("available", False)
            np_peer_median = np_pb.get("peer_median")
            np_transition = growth.get("net_profit_transition_status")

            if np_avail and _is_finite(np_val):
                if np_peer_avail and _is_finite(np_peer_median):
                    np_score_dict["company_value"] = np_val
                    np_score_dict["peer_median"] = np_peer_median
                    np_score_dict["score_source"] = "PEER_RELATIVE_NUMERIC"
                    np_score_dict["score"] = _calculate_relative_score(np_val, np_peer_median)
                    np_score_dict["available"] = True
                else:
                    warnings_set.add("NET_PROFIT_PEER_BASELINE_UNAVAILABLE")
            elif np_transition and np_transition in TRANSITION_SCORES:
                np_score_dict["transition"] = np_transition
                np_score_dict["score_source"] = "TRANSITION_STATUS"
                np_score_dict["score"] = float(TRANSITION_SCORES[np_transition])
                np_score_dict["available"] = True
                warnings_set.add("NET_PROFIT_TRANSITION_SCORE_USED")
            # If net profit data is completely missing (not even transition), it remains unavailable.

            # 3. Final Growth Score
            total_weight = 0.0
            sum_weighted_scores = 0.0
            
            if sales_score_dict["available"]:
                total_weight += 50.0
                sum_weighted_scores += sales_score_dict["score"] * 50.0
                
            if np_score_dict["available"]:
                total_weight += 50.0
                sum_weighted_scores += np_score_dict["score"] * 50.0
                
            final_score = None
            status = "UNAVAILABLE"
            coverage = 0.0
            
            if total_weight > 0:
                final_score = sum_weighted_scores / total_weight
                status = _get_status(final_score)
                coverage = (total_weight / 100.0) * 100.0

            # Warnings Cleanup & Updates
            warnings_set.discard("GROWTH_SCORE_PARTIAL")
            warnings_set.discard("GROWTH_SCORE_UNAVAILABLE")
            
            if total_weight == 0:
                warnings_set.add("GROWTH_SCORE_UNAVAILABLE")
            elif total_weight < 100.0:
                warnings_set.add("GROWTH_SCORE_PARTIAL")

            # Update Record
            new_calc = copy.deepcopy(calc_details)
            fund_scoring = new_calc.setdefault("fundamental_scoring", {})
            fund_scoring["growth"] = {
                "sales_growth": sales_score_dict,
                "net_profit_growth": np_score_dict,
                "available_weight": total_weight,
                "coverage_pct": coverage,
                "score": final_score,
                "status": status
            }
            
            warn_list = list(warnings_set)
            warn_list.sort()
            new_calc["warnings"] = warn_list
            
            rec.calculation_details = new_calc
            rec.growth_score = final_score
            
        self._disc.commit()

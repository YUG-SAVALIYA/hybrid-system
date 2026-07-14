"""
FundamentalProfitabilityScoreService

Calculates fundamental profitability scores based on peer-relative comparisons.
"""
from __future__ import annotations

import logging
import copy
from sqlalchemy.orm import Session

import config
from models.discovery import CompanyFundamentalMetric

logger = logging.getLogger(__name__)

OPERATING_MARGIN_FULL_SCALE_DELTA_PP = getattr(config, 'OPERATING_MARGIN_FULL_SCALE_DELTA_PP', 10.0)
MARGIN_TREND_FULL_SCALE_DELTA_PP = getattr(config, 'MARGIN_TREND_FULL_SCALE_DELTA_PP', 5.0)

def _calculate_relative_score(company_value: float, peer_median: float, scale_delta: float) -> float:
    delta = company_value - peer_median
    score = 50.0 + (delta / scale_delta) * 50.0
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

class FundamentalProfitabilityScoreService:
    def __init__(self, discovery_session: Session):
        self._disc = discovery_session

    def score_profitability(self, run_id: str) -> None:
        records = self._disc.query(CompanyFundamentalMetric).filter_by(run_id=run_id).all()
        if not records:
            return

        for rec in records:
            calc_details = rec.calculation_details or {}
            prof = calc_details.get("profitability", {})
            peer_benchmarks = calc_details.get("peer_benchmarks", {})
            pb_metrics = peer_benchmarks.get("metrics", {})
            warnings_set = set(calc_details.get("warnings", []))
            
            # 1. Operating Margin Score
            om_score_dict = {
                "company_value": None,
                "peer_median": None,
                "peer_delta_pp": None,
                "score": None,
                "available": False
            }
            om_avail = prof.get("latest_operating_margin_available", False)
            om_val = prof.get("latest_operating_margin_pct")
            om_pb = pb_metrics.get("latest_operating_margin_pct", {})
            om_peer_avail = om_pb.get("available", False)
            om_peer_median = om_pb.get("peer_median")
            
            if om_avail and _is_finite(om_val):
                if om_peer_avail and _is_finite(om_peer_median):
                    om_score_dict["company_value"] = om_val
                    om_score_dict["peer_median"] = om_peer_median
                    om_score_dict["peer_delta_pp"] = om_val - om_peer_median
                    om_score_dict["score"] = _calculate_relative_score(om_val, om_peer_median, OPERATING_MARGIN_FULL_SCALE_DELTA_PP)
                    om_score_dict["available"] = True
                else:
                    warnings_set.add("OPERATING_MARGIN_PEER_BASELINE_UNAVAILABLE")

            # 2. Margin Trend Score
            mt_score_dict = {
                "company_value": None,
                "peer_median": None,
                "peer_delta_pp": None,
                "score": None,
                "available": False
            }
            mt_avail = prof.get("operating_margin_trend_available", False)
            mt_val = prof.get("operating_margin_change_pp")
            mt_pb = pb_metrics.get("operating_margin_change_pp", {})
            mt_peer_avail = mt_pb.get("available", False)
            mt_peer_median = mt_pb.get("peer_median")

            if mt_avail and _is_finite(mt_val):
                if mt_peer_avail and _is_finite(mt_peer_median):
                    mt_score_dict["company_value"] = mt_val
                    mt_score_dict["peer_median"] = mt_peer_median
                    mt_score_dict["peer_delta_pp"] = mt_val - mt_peer_median
                    mt_score_dict["score"] = _calculate_relative_score(mt_val, mt_peer_median, MARGIN_TREND_FULL_SCALE_DELTA_PP)
                    mt_score_dict["available"] = True
                else:
                    warnings_set.add("MARGIN_TREND_PEER_BASELINE_UNAVAILABLE")

            # 3. Final Profitability Score
            total_weight = 0.0
            sum_weighted_scores = 0.0
            
            if om_score_dict["available"]:
                total_weight += 60.0
                sum_weighted_scores += om_score_dict["score"] * 60.0
                
            if mt_score_dict["available"]:
                total_weight += 40.0
                sum_weighted_scores += mt_score_dict["score"] * 40.0
                
            final_score = None
            status = "UNAVAILABLE"
            coverage = 0.0
            
            if total_weight > 0:
                final_score = sum_weighted_scores / total_weight
                status = _get_status(final_score)
                coverage = (total_weight / 100.0) * 100.0

            # Warnings Cleanup & Updates
            warnings_set.discard("PROFITABILITY_SCORE_PARTIAL")
            warnings_set.discard("PROFITABILITY_SCORE_UNAVAILABLE")
            
            if total_weight == 0:
                warnings_set.add("PROFITABILITY_SCORE_UNAVAILABLE")
            elif total_weight < 100.0:
                warnings_set.add("PROFITABILITY_SCORE_PARTIAL")

            # Update Record
            new_calc = copy.deepcopy(calc_details)
            fund_scoring = new_calc.setdefault("fundamental_scoring", {})
            fund_scoring["profitability"] = {
                "operating_margin": om_score_dict,
                "margin_trend": mt_score_dict,
                "available_weight": total_weight,
                "coverage_pct": coverage,
                "score": final_score,
                "status": status
            }
            
            warn_list = list(warnings_set)
            warn_list.sort()
            new_calc["warnings"] = warn_list
            
            rec.calculation_details = new_calc
            rec.profitability_score = final_score
            
        self._disc.commit()

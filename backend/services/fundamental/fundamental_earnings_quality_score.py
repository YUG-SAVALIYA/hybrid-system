"""
FundamentalEarningsQualityScoreService

Calculates deterministic company earnings-quality scoring.
"""
from __future__ import annotations

import logging
import copy
from sqlalchemy.orm import Session

import config
from models.discovery import CompanyFundamentalMetric

logger = logging.getLogger(__name__)

OCF_TO_PAT_FULL_SCALE_DIFFERENCE = getattr(config, 'OCF_TO_PAT_FULL_SCALE_DIFFERENCE', 1.0)
OCF_TO_PAT_TREND_FULL_SCALE_DIFFERENCE = getattr(config, 'OCF_TO_PAT_TREND_FULL_SCALE_DIFFERENCE', 0.50)
PAT_GROWTH_VOLATILITY_FULL_SCALE_DELTA_PP = getattr(config, 'PAT_GROWTH_VOLATILITY_FULL_SCALE_DELTA_PP', 50.0)

TRANSITION_SCORES = {
    "LOSS_WITH_POSITIVE_OPERATING_CASH_FLOW": 65,
    "LOSS_WITH_ZERO_OPERATING_CASH_FLOW": 25,
    "LOSS_WITH_NEGATIVE_OPERATING_CASH_FLOW": 5,
    "ZERO_PAT_WITH_POSITIVE_OPERATING_CASH_FLOW": 60,
    "ZERO_PAT_WITH_ZERO_OPERATING_CASH_FLOW": 30,
    "ZERO_PAT_WITH_NEGATIVE_OPERATING_CASH_FLOW": 5,
    "PROFIT_WITH_ZERO_OPERATING_CASH_FLOW": 15,
    "NEGATIVE_OPERATING_CASH_FLOW": 0
}

def _calculate_score(delta: float, scale_delta: float) -> float:
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

class FundamentalEarningsQualityScoreService:
    def __init__(self, discovery_session: Session):
        self._disc = discovery_session

    def score_earnings_quality(self, run_id: str) -> None:
        records = self._disc.query(CompanyFundamentalMetric).filter_by(run_id=run_id).all()
        if not records:
            return

        for rec in records:
            calc_details = rec.calculation_details or {}
            eq = calc_details.get("earnings_quality", {})
            cc = eq.get("cash_conversion", {})
            ps = eq.get("profit_stability", {})
            
            peer_benchmarks = calc_details.get("peer_benchmarks", {})
            pb_metrics = peer_benchmarks.get("metrics", {})
            warnings_set = set(calc_details.get("warnings", []))
            
            # 1. Latest cash conversion (40%)
            cc_score_dict = {
                "company_value": None,
                "peer_median": None,
                "peer_delta": None,
                "status": None,
                "score_source": "UNAVAILABLE",
                "score": None,
                "available": False
            }
            cc_avail = cc.get("latest_ocf_to_pat_available", False)
            cc_val = cc.get("latest_ocf_to_pat")
            cc_pb = pb_metrics.get("latest_ocf_to_pat", {})
            cc_peer_avail = cc_pb.get("available", False)
            cc_peer_median = cc_pb.get("peer_median")
            cc_status = cc.get("latest_cash_conversion_status")
            
            # Note: Do not assign fallback scores when PAT or OCF is missing.
            # If cc_status is missing, we don't apply a fallback.
            
            if cc_avail and _is_finite(cc_val):
                if cc_peer_avail and _is_finite(cc_peer_median):
                    delta = cc_val - cc_peer_median
                    cc_score_dict["company_value"] = cc_val
                    cc_score_dict["peer_median"] = cc_peer_median
                    cc_score_dict["peer_delta"] = delta
                    cc_score_dict["score_source"] = "PEER_RELATIVE_NUMERIC"
                    cc_score_dict["score"] = _calculate_score(delta, OCF_TO_PAT_FULL_SCALE_DIFFERENCE)
                    cc_score_dict["available"] = True
                else:
                    warnings_set.add("CASH_CONVERSION_PEER_BASELINE_UNAVAILABLE")
            elif cc_status and cc_status in TRANSITION_SCORES:
                # If numeric ratio is unavailable because PAT is zero/negative
                cc_score_dict["status"] = cc_status
                cc_score_dict["score_source"] = "STATUS_FALLBACK"
                cc_score_dict["score"] = float(TRANSITION_SCORES[cc_status])
                cc_score_dict["available"] = True
                warnings_set.add("CASH_CONVERSION_STATUS_SCORE_USED")

            # 2. Cash-conversion trend score (20%)
            cct_score_dict = {
                "company_value": None,
                "peer_median": None,
                "peer_delta": None,
                "score": None,
                "available": False
            }
            cct_avail = cc.get("ocf_to_pat_change_available", False)
            cct_val = cc.get("ocf_to_pat_change")
            cct_pb = pb_metrics.get("ocf_to_pat_change", {})
            cct_peer_avail = cct_pb.get("available", False)
            cct_peer_median = cct_pb.get("peer_median")
            
            if cct_avail and _is_finite(cct_val):
                if cct_peer_avail and _is_finite(cct_peer_median):
                    delta = cct_val - cct_peer_median
                    cct_score_dict["company_value"] = cct_val
                    cct_score_dict["peer_median"] = cct_peer_median
                    cct_score_dict["peer_delta"] = delta
                    cct_score_dict["score"] = _calculate_score(delta, OCF_TO_PAT_TREND_FULL_SCALE_DIFFERENCE)
                    cct_score_dict["available"] = True
                else:
                    warnings_set.add("CASH_CONVERSION_TREND_PEER_BASELINE_UNAVAILABLE")

            # 3. Profit-history score (25%)
            ph_score_dict = {
                "positive_pat_period_ratio": None,
                "score": None,
                "available": False
            }
            ph_avail = ps.get("profit_stability_available", False)
            ph_val = ps.get("positive_pat_period_ratio")
            
            if ph_avail and _is_finite(ph_val):
                ph_score_dict["positive_pat_period_ratio"] = ph_val
                ph_score_dict["score"] = max(0.0, min(100.0, float(ph_val)))
                ph_score_dict["available"] = True

            # 4. PAT-growth-volatility score (15%)
            vol_score_dict = {
                "company_value": None,
                "peer_median": None,
                "peer_advantage_pp": None,
                "score": None,
                "available": False
            }
            vol_avail = ps.get("pat_growth_volatility_available", False)
            vol_val = ps.get("pat_growth_volatility_pct")
            vol_pb = pb_metrics.get("pat_growth_volatility_pct", {})
            vol_peer_avail = vol_pb.get("available", False)
            vol_peer_median = vol_pb.get("peer_median")
            
            if vol_avail and _is_finite(vol_val):
                if vol_peer_avail and _is_finite(vol_peer_median):
                    advantage_pp = vol_peer_median - vol_val
                    vol_score_dict["company_value"] = vol_val
                    vol_score_dict["peer_median"] = vol_peer_median
                    vol_score_dict["peer_advantage_pp"] = advantage_pp
                    vol_score_dict["score"] = _calculate_score(advantage_pp, PAT_GROWTH_VOLATILITY_FULL_SCALE_DELTA_PP)
                    vol_score_dict["available"] = True
                else:
                    warnings_set.add("PAT_VOLATILITY_PEER_BASELINE_UNAVAILABLE")

            # 5. Final earnings-quality score
            total_weight = 0.0
            sum_weighted_scores = 0.0
            
            if cc_score_dict["available"]:
                total_weight += 40.0
                sum_weighted_scores += cc_score_dict["score"] * 40.0
                
            if cct_score_dict["available"]:
                total_weight += 20.0
                sum_weighted_scores += cct_score_dict["score"] * 20.0
                
            if ph_score_dict["available"]:
                total_weight += 25.0
                sum_weighted_scores += ph_score_dict["score"] * 25.0
                
            if vol_score_dict["available"]:
                total_weight += 15.0
                sum_weighted_scores += vol_score_dict["score"] * 15.0

            final_score = None
            status = "UNAVAILABLE"
            coverage = 0.0
            
            if total_weight > 0:
                final_score = sum_weighted_scores / total_weight
                status = _get_status(final_score)
                coverage = (total_weight / 100.0) * 100.0

            # Warnings Cleanup & Updates
            warnings_set.discard("EARNINGS_QUALITY_SCORE_PARTIAL")
            warnings_set.discard("EARNINGS_QUALITY_SCORE_UNAVAILABLE")
            
            if total_weight == 0:
                warnings_set.add("EARNINGS_QUALITY_SCORE_UNAVAILABLE")
            elif total_weight < 100.0:
                warnings_set.add("EARNINGS_QUALITY_SCORE_PARTIAL")

            new_calc = copy.deepcopy(calc_details)
            fund_scoring = new_calc.setdefault("fundamental_scoring", {})
            fund_scoring["earnings_quality"] = {
                "latest_cash_conversion": cc_score_dict,
                "cash_conversion_trend": cct_score_dict,
                "profit_history": ph_score_dict,
                "pat_growth_volatility": vol_score_dict,
                "available_weight": total_weight,
                "coverage_pct": coverage,
                "score": final_score,
                "status": status
            }
            
            warn_list = list(warnings_set)
            warn_list.sort()
            new_calc["warnings"] = warn_list
            
            rec.calculation_details = new_calc
            rec.earnings_quality_score = final_score
            
        self._disc.commit()

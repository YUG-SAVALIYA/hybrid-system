"""
FundamentalFinancialStrengthScoreService

Calculates deterministic company financial-strength scoring.
"""
from __future__ import annotations

import logging
import copy
from sqlalchemy.orm import Session

import config
from models.discovery import CompanyFundamentalMetric

logger = logging.getLogger(__name__)

DEBT_TO_EQUITY_FULL_SCALE_DIFFERENCE = getattr(config, 'DEBT_TO_EQUITY_FULL_SCALE_DIFFERENCE', 1.0)
BORROWING_CHANGE_FULL_SCALE_DELTA_PP = getattr(config, 'BORROWING_CHANGE_FULL_SCALE_DELTA_PP', 50.0)

TRANSITION_SCORES = {
    "ZERO_TO_ZERO": 90,
    "ZERO_TO_POSITIVE": 15
}

def _calculate_score(advantage: float, scale_delta: float) -> float:
    score = 50.0 + (advantage / scale_delta) * 50.0
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

class FundamentalFinancialStrengthScoreService:
    def __init__(self, discovery_session: Session):
        self._disc = discovery_session

    def score_financial_strength(self, run_id: str) -> None:
        records = self._disc.query(CompanyFundamentalMetric).filter_by(run_id=run_id).all()
        if not records:
            return

        for rec in records:
            calc_details = rec.calculation_details or {}
            fs = calc_details.get("financial_strength", {})
            peer_benchmarks = calc_details.get("peer_benchmarks", {})
            pb_metrics = peer_benchmarks.get("metrics", {})
            warnings_set = set(calc_details.get("warnings", []))
            
            # 1. Financial-business applicability
            std_debt_applicable = fs.get("standard_debt_rule_applicable", True)
            
            new_calc = copy.deepcopy(calc_details)
            fund_scoring = new_calc.setdefault("fundamental_scoring", {})
            
            if not std_debt_applicable:
                warnings_set.add("STANDARD_DEBT_RULE_SCORE_NOT_APPLICABLE")
                fund_scoring["financial_strength"] = {
                    "applicable": False,
                    "available_weight": None,
                    "coverage_pct": None,
                    "score": None,
                    "status": "N_A"
                }
                warn_list = list(warnings_set)
                warn_list.sort()
                new_calc["warnings"] = warn_list
                rec.calculation_details = new_calc
                rec.financial_strength_score = None
                continue

            # Standard non-financial processing
            
            # 2. Debt-to-equity score
            dte_score_dict = {
                "company_value": None,
                "peer_median": None,
                "peer_advantage": None,
                "score": None,
                "available": False
            }
            dte_avail = fs.get("debt_to_equity_available", False)
            dte_val = fs.get("debt_to_equity")
            dte_pb = pb_metrics.get("debt_to_equity", {})
            dte_peer_avail = dte_pb.get("available", False)
            dte_peer_median = dte_pb.get("peer_median")
            
            if dte_avail and _is_finite(dte_val):
                if dte_peer_avail and _is_finite(dte_peer_median):
                    advantage = dte_peer_median - dte_val
                    dte_score_dict["company_value"] = dte_val
                    dte_score_dict["peer_median"] = dte_peer_median
                    dte_score_dict["peer_advantage"] = advantage
                    dte_score_dict["score"] = _calculate_score(advantage, DEBT_TO_EQUITY_FULL_SCALE_DIFFERENCE)
                    dte_score_dict["available"] = True
                else:
                    warnings_set.add("DEBT_TO_EQUITY_PEER_BASELINE_UNAVAILABLE")
            
            # 3. Borrowing-trend score
            bt_score_dict = {
                "company_value": None,
                "peer_median": None,
                "peer_advantage_pp": None,
                "transition": None,
                "score_source": "UNAVAILABLE",
                "score": None,
                "available": False
            }
            bt_avail = fs.get("borrowing_trend_available", False)
            bt_val = fs.get("borrowing_change_pct")
            bt_pb = pb_metrics.get("borrowing_change_pct", {})
            bt_peer_avail = bt_pb.get("available", False)
            bt_peer_median = bt_pb.get("peer_median")
            bt_trans = fs.get("borrowing_transition")
            
            if bt_avail and _is_finite(bt_val):
                if bt_peer_avail and _is_finite(bt_peer_median):
                    advantage_pp = bt_peer_median - bt_val
                    bt_score_dict["company_value"] = bt_val
                    bt_score_dict["peer_median"] = bt_peer_median
                    bt_score_dict["peer_advantage_pp"] = advantage_pp
                    bt_score_dict["score_source"] = "PEER_RELATIVE_NUMERIC"
                    bt_score_dict["score"] = _calculate_score(advantage_pp, BORROWING_CHANGE_FULL_SCALE_DELTA_PP)
                    bt_score_dict["available"] = True
                else:
                    warnings_set.add("BORROWING_TREND_PEER_BASELINE_UNAVAILABLE")
            elif bt_trans and bt_trans in TRANSITION_SCORES:
                bt_score_dict["transition"] = bt_trans
                bt_score_dict["score_source"] = "TRANSITION_STATUS"
                bt_score_dict["score"] = float(TRANSITION_SCORES[bt_trans])
                bt_score_dict["available"] = True
                warnings_set.add("BORROWING_TRANSITION_SCORE_USED")

            # 5. Final financial-strength score
            total_weight = 0.0
            sum_weighted_scores = 0.0
            
            if dte_score_dict["available"]:
                total_weight += 60.0
                sum_weighted_scores += dte_score_dict["score"] * 60.0
                
            if bt_score_dict["available"]:
                total_weight += 40.0
                sum_weighted_scores += bt_score_dict["score"] * 40.0
                
            final_score = None
            status = "UNAVAILABLE"
            coverage = 0.0
            
            if total_weight > 0:
                final_score = sum_weighted_scores / total_weight
                status = _get_status(final_score)
                coverage = (total_weight / 100.0) * 100.0

            # Warnings Cleanup & Updates
            warnings_set.discard("FINANCIAL_STRENGTH_SCORE_PARTIAL")
            warnings_set.discard("FINANCIAL_STRENGTH_SCORE_UNAVAILABLE")
            
            if total_weight == 0:
                warnings_set.add("FINANCIAL_STRENGTH_SCORE_UNAVAILABLE")
            elif total_weight < 100.0:
                warnings_set.add("FINANCIAL_STRENGTH_SCORE_PARTIAL")

            fund_scoring["financial_strength"] = {
                "applicable": True,
                "debt_to_equity": dte_score_dict,
                "borrowing_trend": bt_score_dict,
                "available_weight": total_weight,
                "coverage_pct": coverage,
                "score": final_score,
                "status": status
            }
            
            warn_list = list(warnings_set)
            warn_list.sort()
            new_calc["warnings"] = warn_list
            
            rec.calculation_details = new_calc
            rec.financial_strength_score = final_score
            
        self._disc.commit()

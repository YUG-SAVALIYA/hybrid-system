"""
FundamentalBasicIndustryTransitionScoreService

Calculates deterministic structural transition scores for basic industries.
"""
from __future__ import annotations

import logging
import copy
from typing import Dict, Any, List
from sqlalchemy.orm import Session

from models.discovery import GroupScore

logger = logging.getLogger(__name__)

NP_NUMERIC = {"STANDARD_GROWTH"}
NP_FALLBACK = {
    "LOSS_TO_PROFIT": 90.0,
    "LOSS_NARROWED": 65.0,
    "LOSS_UNCHANGED": 35.0,
    "LOSS_WIDENED": 10.0,
    "ZERO_BASE_TO_PROFIT": 85.0,
    "ZERO_BASE_TO_LOSS": 10.0,
    "ZERO_BASE_UNCHANGED": 30.0
}
NP_EXCLUDE = set()

BOR_NUMERIC = {"INCREASED", "DECREASED", "UNCHANGED"}
BOR_FALLBACK = {
    "ZERO_TO_ZERO": 90.0,
    "ZERO_TO_POSITIVE": 15.0
}
BOR_EXCLUDE = {"INVALID_NEGATIVE_BORROWINGS", "UNAVAILABLE"}

CC_NUMERIC = {
    "STRONG_CASH_CONVERSION",
    "ADEQUATE_CASH_CONVERSION",
    "WEAK_CASH_CONVERSION",
    "PROFIT_WITH_ZERO_OPERATING_CASH_FLOW",
    "NEGATIVE_OPERATING_CASH_FLOW"
}
CC_FALLBACK = {
    "LOSS_WITH_POSITIVE_OPERATING_CASH_FLOW": 65.0,
    "LOSS_WITH_ZERO_OPERATING_CASH_FLOW": 25.0,
    "LOSS_WITH_NEGATIVE_OPERATING_CASH_FLOW": 5.0,
    "ZERO_PAT_WITH_POSITIVE_OPERATING_CASH_FLOW": 60.0,
    "ZERO_PAT_WITH_ZERO_OPERATING_CASH_FLOW": 30.0,
    "ZERO_PAT_WITH_NEGATIVE_OPERATING_CASH_FLOW": 5.0
}
CC_EXCLUDE = {"UNAVAILABLE"}

class FundamentalBasicIndustryTransitionScoreService:
    def __init__(self, discovery_session: Session):
        self._disc = discovery_session

    def _process_transition(self, 
                            counts_dict: Dict[str, int], 
                            numeric_set: set, 
                            fallback_dict: Dict[str, float], 
                            exclude_set: set,
                            is_debt: bool,
                            std_debt_app_cnt: int) -> Dict[str, Any]:
                            
        if is_debt and std_debt_app_cnt == 0:
            return {
                "valid_status_count": 0,
                "numeric_status_count": 0,
                "fallback_status_count": 0,
                "excluded_status_count": 0,
                "numeric_share_pct": None,
                "fallback_share_pct": None,
                "fallback_score": None,
                "applicable": False,
                "available": False,
                "reason": "N_A_NO_STANDARD_DEBT_RULE_COMPANIES",
                "contributions": {}
            }
            
        num_cnt = 0
        fall_cnt = 0
        excl_cnt = 0
        
        weighted_sum = 0.0
        conts = {}
        
        for st, c in counts_dict.items():
            if st in numeric_set:
                num_cnt += c
            elif st in fallback_dict:
                fall_cnt += c
                w = fallback_dict[st]
                weighted_sum += (c * w)
                conts[st] = {"count": c, "configured_score": w}
            elif st in exclude_set or st == "UNAVAILABLE":
                excl_cnt += c
                
        valid_cnt = num_cnt + fall_cnt
        
        num_share = None
        fall_share = None
        if valid_cnt > 0:
            num_share = round((num_cnt / valid_cnt) * 100.0, 2)
            fall_share = round((fall_cnt / valid_cnt) * 100.0, 2)
            
        score = None
        avail = False
        reason = None
        
        if fall_cnt > 0:
            score = round(weighted_sum / fall_cnt, 2)
            avail = True
        else:
            reason = "NO_STRUCTURAL_FALLBACK_OBSERVATIONS"
            
        return {
            "valid_status_count": valid_cnt,
            "numeric_status_count": num_cnt,
            "fallback_status_count": fall_cnt,
            "excluded_status_count": excl_cnt,
            "numeric_share_pct": num_share,
            "fallback_share_pct": fall_share,
            "fallback_score": score,
            "applicable": True,
            "available": avail,
            "reason": reason,
            "contributions": conts
        }

    def calculate_basic_industry_transitions(
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
            
        for bi in bi_groups:
            calc = copy.deepcopy(bi.calculation_details or {})
            raw_agg = calc.get("fundamental", {}).get("raw_aggregation", {})
            std_debt_app_cnt = raw_agg.get("standard_debt_rule_applicable_count", 0)
            transitions = raw_agg.get("transitions", {})
            
            np_counts = transitions.get("net_profit", {}).get("counts", {})
            bor_counts = transitions.get("borrowing", {}).get("counts", {})
            cc_counts = transitions.get("cash_conversion", {}).get("counts", {})
            
            np_res = self._process_transition(np_counts, NP_NUMERIC, NP_FALLBACK, NP_EXCLUDE, False, 0)
            bor_res = self._process_transition(bor_counts, BOR_NUMERIC, BOR_FALLBACK, BOR_EXCLUDE, True, std_debt_app_cnt)
            cc_res = self._process_transition(cc_counts, CC_NUMERIC, CC_FALLBACK, CC_EXCLUDE, False, 0)
            
            if "fundamental" not in calc:
                calc["fundamental"] = {}
                
            calc["fundamental"]["structural_transition_scores"] = {
                "net_profit": np_res,
                "borrowing": bor_res,
                "cash_conversion": cc_res
            }
            
            bi.calculation_details = calc
            
        self._disc.commit()

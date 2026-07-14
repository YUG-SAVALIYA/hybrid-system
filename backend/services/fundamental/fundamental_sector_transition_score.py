"""
FundamentalSectorTransitionScoreService

Calculates deterministic structural transition scores for sectors.
"""
from __future__ import annotations

import logging
import copy
from typing import Dict, Any
from sqlalchemy.orm import Session

from models.discovery import GroupScore

logger = logging.getLogger(__name__)

# Configured mappings
NET_PROFIT_NUMERIC = {"STANDARD_GROWTH"}
NET_PROFIT_FALLBACKS = {
    "LOSS_TO_PROFIT": 90.0,
    "LOSS_NARROWED": 65.0,
    "LOSS_UNCHANGED": 35.0,
    "LOSS_WIDENED": 10.0,
    "ZERO_BASE_TO_PROFIT": 85.0,
    "ZERO_BASE_TO_LOSS": 10.0,
    "ZERO_BASE_UNCHANGED": 30.0
}
NET_PROFIT_EXCLUDED = set()

BORROWING_NUMERIC = {"INCREASED", "DECREASED", "UNCHANGED"}
BORROWING_FALLBACKS = {
    "ZERO_TO_ZERO": 90.0,
    "ZERO_TO_POSITIVE": 15.0
}
BORROWING_EXCLUDED = {"INVALID_NEGATIVE_BORROWINGS", "UNAVAILABLE"}

CASH_CONVERSION_NUMERIC = {
    "STRONG_CASH_CONVERSION",
    "ADEQUATE_CASH_CONVERSION",
    "WEAK_CASH_CONVERSION",
    "PROFIT_WITH_ZERO_OPERATING_CASH_FLOW",
    "NEGATIVE_OPERATING_CASH_FLOW"
}
CASH_CONVERSION_FALLBACKS = {
    "LOSS_WITH_POSITIVE_OPERATING_CASH_FLOW": 65.0,
    "LOSS_WITH_ZERO_OPERATING_CASH_FLOW": 25.0,
    "LOSS_WITH_NEGATIVE_OPERATING_CASH_FLOW": 5.0,
    "ZERO_PAT_WITH_POSITIVE_OPERATING_CASH_FLOW": 60.0,
    "ZERO_PAT_WITH_ZERO_OPERATING_CASH_FLOW": 30.0,
    "ZERO_PAT_WITH_NEGATIVE_OPERATING_CASH_FLOW": 5.0
}
CASH_CONVERSION_EXCLUDED = {"UNAVAILABLE"}

def _process_category(counts: Dict[str, int], 
                      numeric_set: set, 
                      fallback_map: Dict[str, float], 
                      excluded_set: set,
                      applicable: bool = True,
                      reason: str | None = None) -> Dict[str, Any]:
                      
    if not applicable:
        return {
            "valid_status_count": 0,
            "numeric_status_count": 0,
            "fallback_status_count": 0,
            "excluded_status_count": 0,
            "numeric_share_pct": 0.0,
            "fallback_share_pct": 0.0,
            "fallback_score": None,
            "applicable": False,
            "available": False,
            "reason": reason,
            "contributions": {}
        }
        
    valid_count = 0
    num_count = 0
    fall_count = 0
    excl_count = 0
    
    total_fall_score = 0.0
    contributions = {}
    
    for status, count in counts.items():
        if count <= 0:
            continue
            
        if status in excluded_set:
            excl_count += count
        elif status in numeric_set:
            num_count += count
            valid_count += count
        elif status in fallback_map:
            fall_count += count
            valid_count += count
            
            c_score = fallback_map[status]
            total_fall_score += count * c_score
            contributions[status] = {
                "count": count,
                "configured_score": c_score
            }
        else:
            # Treat unknown as excluded safely
            excl_count += count
            
    num_share = 0.0
    fall_share = 0.0
    if valid_count > 0:
        num_share = round((num_count / valid_count) * 100.0, 2)
        fall_share = round((fall_count / valid_count) * 100.0, 2)
        
    fallback_score = None
    if fall_count > 0:
        fallback_score = round(total_fall_score / fall_count, 2)
        
    return {
        "valid_status_count": valid_count,
        "numeric_status_count": num_count,
        "fallback_status_count": fall_count,
        "excluded_status_count": excl_count,
        "numeric_share_pct": num_share,
        "fallback_share_pct": fall_share,
        "fallback_score": fallback_score,
        "applicable": True,
        "available": (fall_count > 0),
        "reason": None,
        "contributions": contributions
    }

class FundamentalSectorTransitionScoreService:
    def __init__(self, discovery_session: Session):
        self._disc = discovery_session

    def calculate_transition_scores(self, run_id: str) -> None:
        sectors = self._disc.query(GroupScore).filter_by(run_id=run_id, entity_type="SECTOR").all()
        if not sectors:
            return

        for sector in sectors:
            calc_details = copy.deepcopy(sector.calculation_details or {})
            fund = calc_details.get("fundamental", {})
            raw_agg = fund.get("raw_aggregation", {})
            transitions = raw_agg.get("transitions", {})
            
            std_debt_app_cnt = raw_agg.get("standard_debt_rule_applicable_count", 0)
            
            # 1. Net profit
            np_counts = transitions.get("net_profit", {}).get("counts", {})
            np_result = _process_category(np_counts, NET_PROFIT_NUMERIC, NET_PROFIT_FALLBACKS, NET_PROFIT_EXCLUDED)
            
            # 2. Borrowing
            b_counts = transitions.get("borrowing", {}).get("counts", {})
            b_applicable = (std_debt_app_cnt > 0)
            b_reason = "N_A_NO_STANDARD_DEBT_RULE_COMPANIES" if not b_applicable else None
            b_result = _process_category(b_counts, BORROWING_NUMERIC, BORROWING_FALLBACKS, BORROWING_EXCLUDED, 
                                         applicable=b_applicable, reason=b_reason)
                                         
            # 3. Cash conversion
            cc_counts = transitions.get("cash_conversion", {}).get("counts", {})
            cc_result = _process_category(cc_counts, CASH_CONVERSION_NUMERIC, CASH_CONVERSION_FALLBACKS, CASH_CONVERSION_EXCLUDED)

            if "fundamental" not in calc_details:
                calc_details["fundamental"] = {}
                
            calc_details["fundamental"]["structural_transition_scores"] = {
                "net_profit": np_result,
                "borrowing": b_result,
                "cash_conversion": cc_result
            }
            
            sector.calculation_details = calc_details
            
        self._disc.commit()

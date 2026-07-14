"""
Tests for FundamentalSectorTransitionScoreService.
"""
import uuid
import pytest
from sqlalchemy import text

from database import DiscoverySessionLocal
from models.discovery import GroupScore
from services.fundamental.fundamental_sector_transition_score import FundamentalSectorTransitionScoreService

@pytest.fixture
def disc_session():
    session = DiscoverySessionLocal()
    session.execute(text("DELETE FROM group_scores"))
    session.commit()
    yield session
    session.execute(text("DELETE FROM group_scores"))
    session.commit()
    session.close()

def _create_group(session, run_id, entity_name, np_counts, b_counts, cc_counts, std_debt_cnt=10):
    calc = {
        "technical": {"unchanged": True},
        "macro": {"unchanged": True},
        "fundamental": {
            "raw_aggregation": {
                "standard_debt_rule_applicable_count": std_debt_cnt,
                "transitions": {
                    "net_profit": {"counts": np_counts},
                    "borrowing": {"counts": b_counts},
                    "cash_conversion": {"counts": cc_counts}
                }
            },
            "metric_normalization": {"unchanged": True}
        }
    }
        
    g = GroupScore(
        id=str(uuid.uuid4()),
        run_id=run_id,
        entity_type="SECTOR",
        entity_name=entity_name,
        parent_sector="",
        parent_industry="",
        horizon="1Y",
        calculation_details=calc
    )
    session.add(g)

def test_fundamental_sector_transition_score_service(disc_session):
    run_id = "run_trans"
    
    # 1. Net-profit fallback weighted score, 2. STANDARD_GROWTH numeric, 11. Share calculations
    _create_group(disc_session, run_id, "SEC_MIXED", 
        np_counts={
            "STANDARD_GROWTH": 4, # Numeric
            "LOSS_TO_PROFIT": 4, # 90
            "LOSS_WIDENED": 2 # 10
        },
        b_counts={},
        cc_counts={}
    )
    
    # 3. No fallback net-profit -> null score
    _create_group(disc_session, run_id, "SEC_NUMERIC_ONLY", 
        np_counts={"STANDARD_GROWTH": 10},
        b_counts={},
        cc_counts={}
    )
    
    # 4. Every net-profit mapping
    _create_group(disc_session, run_id, "SEC_ALL_NP", 
        np_counts={
            "LOSS_TO_PROFIT": 1, # 90
            "LOSS_NARROWED": 1, # 65
            "LOSS_UNCHANGED": 1, # 35
            "LOSS_WIDENED": 1, # 10
            "ZERO_BASE_TO_PROFIT": 1, # 85
            "ZERO_BASE_TO_LOSS": 1, # 10
            "ZERO_BASE_UNCHANGED": 1 # 30
        },
        b_counts={},
        cc_counts={}
    )
    
    # 5. Borrowing Z-Z, 6. Borrowing Z-P, 7. Invalid excluded
    _create_group(disc_session, run_id, "SEC_BORROW", 
        np_counts={},
        b_counts={
            "ZERO_TO_ZERO": 2, # 90
            "ZERO_TO_POSITIVE": 2, # 15
            "INCREASED": 4, # Numeric
            "INVALID_NEGATIVE_BORROWINGS": 1, # Excluded
            "UNAVAILABLE": 1 # Excluded
        },
        cc_counts={}
    )
    
    # 8. Financial-only borrowing
    _create_group(disc_session, run_id, "SEC_FINANCIAL", 
        np_counts={},
        b_counts={"ZERO_TO_ZERO": 2},
        cc_counts={},
        std_debt_cnt=0
    )
    
    # 9. Every CC mapping, 10. Positive PAT numeric
    _create_group(disc_session, run_id, "SEC_ALL_CC", 
        np_counts={},
        b_counts={},
        cc_counts={
            "STRONG_CASH_CONVERSION": 2, # Numeric
            "PROFIT_WITH_ZERO_OPERATING_CASH_FLOW": 2, # Numeric
            "LOSS_WITH_POSITIVE_OPERATING_CASH_FLOW": 1, # 65
            "LOSS_WITH_ZERO_OPERATING_CASH_FLOW": 1, # 25
            "LOSS_WITH_NEGATIVE_OPERATING_CASH_FLOW": 1, # 5
            "ZERO_PAT_WITH_POSITIVE_OPERATING_CASH_FLOW": 1, # 60
            "ZERO_PAT_WITH_ZERO_OPERATING_CASH_FLOW": 1, # 30
            "ZERO_PAT_WITH_NEGATIVE_OPERATING_CASH_FLOW": 1 # 5
        }
    )
    
    # 12. Empty distribution
    _create_group(disc_session, run_id, "SEC_EMPTY", {}, {}, {})
    
    disc_session.commit()
    
    svc = FundamentalSectorTransitionScoreService(disc_session)
    svc.calculate_transition_scores(run_id)
    
    def get_t(name):
        g = disc_session.query(GroupScore).filter_by(entity_name=name).first()
        return g.calculation_details["fundamental"]["structural_transition_scores"]

    # 1, 2, 11
    t_mixed = get_t("SEC_MIXED")["net_profit"]
    assert t_mixed["valid_status_count"] == 10
    assert t_mixed["numeric_status_count"] == 4
    assert t_mixed["fallback_status_count"] == 6
    assert t_mixed["numeric_share_pct"] == 40.0
    assert t_mixed["fallback_share_pct"] == 60.0
    # Score: (4*90 + 2*10) / 6 = (360 + 20) / 6 = 380 / 6 = 63.33
    assert t_mixed["fallback_score"] == 63.33
    assert t_mixed["available"] is True
    
    # 3
    t_num = get_t("SEC_NUMERIC_ONLY")["net_profit"]
    assert t_num["fallback_score"] is None
    assert t_num["available"] is False
    assert t_num["numeric_share_pct"] == 100.0
    
    # 4
    t_all_np = get_t("SEC_ALL_NP")["net_profit"]
    # (90+65+35+10+85+10+30) = 325. 325 / 7 = 46.43
    assert t_all_np["fallback_score"] == 46.43
    
    # 5, 6, 7
    t_bor = get_t("SEC_BORROW")["borrowing"]
    assert t_bor["valid_status_count"] == 8
    assert t_bor["excluded_status_count"] == 2
    assert t_bor["numeric_status_count"] == 4
    assert t_bor["fallback_status_count"] == 4
    # Score: (2*90 + 2*15) / 4 = 210 / 4 = 52.5
    assert t_bor["fallback_score"] == 52.5
    
    # 8
    t_fin = get_t("SEC_FINANCIAL")["borrowing"]
    assert t_fin["applicable"] is False
    assert t_fin["reason"] == "N_A_NO_STANDARD_DEBT_RULE_COMPANIES"
    
    # 9, 10
    t_cc = get_t("SEC_ALL_CC")["cash_conversion"]
    assert t_cc["numeric_status_count"] == 4
    assert t_cc["fallback_status_count"] == 6
    # Score: (65+25+5+60+30+5) / 6 = 190 / 6 = 31.67
    assert t_cc["fallback_score"] == 31.67
    
    # 12
    t_emp = get_t("SEC_EMPTY")["net_profit"]
    assert t_emp["valid_status_count"] == 0
    assert t_emp["fallback_score"] is None
    
    # 13, 14
    g = disc_session.query(GroupScore).filter_by(entity_name="SEC_MIXED").first()
    assert g.calculation_details["technical"]["unchanged"] is True
    assert g.calculation_details["macro"]["unchanged"] is True
    assert "raw_aggregation" in g.calculation_details["fundamental"]
    assert g.calculation_details["fundamental"]["metric_normalization"]["unchanged"] is True
    
    # 15
    svc.calculate_transition_scores(run_id)
    t_mixed2 = get_t("SEC_MIXED")["net_profit"]
    assert t_mixed2["fallback_score"] == 63.33

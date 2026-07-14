"""
Tests for FundamentalBasicIndustryTransitionScoreService.
"""
import uuid
import pytest
from sqlalchemy import text

from database import DiscoverySessionLocal
from models.discovery import GroupScore
from services.fundamental.fundamental_basic_industry_transition_score import FundamentalBasicIndustryTransitionScoreService

@pytest.fixture
def disc_session():
    session = DiscoverySessionLocal()
    session.execute(text("DELETE FROM group_scores"))
    session.commit()
    yield session
    session.execute(text("DELETE FROM group_scores"))
    session.commit()
    session.close()

def _create_bi(session, run_id, entity_name, np_counts, bor_counts, cc_counts, std_debt_cnt=5):
    calc = {
        "technical": {"unchanged": True},
        "macro": {"unchanged": True},
        "fundamental": {
            "raw_aggregation": {
                "standard_debt_rule_applicable_count": std_debt_cnt,
                "transitions": {
                    "net_profit": {"counts": np_counts},
                    "borrowing": {"counts": bor_counts},
                    "cash_conversion": {"counts": cc_counts}
                },
                "unchanged": True
            },
            "metric_normalization": {"unchanged": True}
        }
    }
        
    g = GroupScore(
        id=str(uuid.uuid4()),
        run_id=run_id,
        entity_type="BASIC_INDUSTRY",
        entity_name=entity_name,
        parent_sector="SecA",
        parent_industry="IndA",
        horizon="1Y",
        calculation_details=calc
    )
    session.add(g)

def test_fundamental_basic_industry_transition_score_service(disc_session):
    run_id = "run_bi_trans"
    
    # 1. Net profit weighted fallback, 2. Every NP fallback, 3. STANDARD_GROWTH is numeric, 12. Shares
    _create_bi(disc_session, run_id, "BI_NP", 
        np_counts={
            "STANDARD_GROWTH": 4, # Numeric
            "LOSS_TO_PROFIT": 1, # 90
            "LOSS_NARROWED": 1, # 65
            "LOSS_UNCHANGED": 1, # 35
            "LOSS_WIDENED": 1, # 10
            "ZERO_BASE_TO_PROFIT": 1, # 85
            "ZERO_BASE_TO_LOSS": 1, # 10
            "ZERO_BASE_UNCHANGED": 2 # 30*2 = 60
        }, # Total fallback score = (90+65+35+10+85+10+60)/8 = 355/8 = 44.375 -> 44.38
        bor_counts={},
        cc_counts={}
    )
    
    # 4. No NP fallback -> null
    _create_bi(disc_session, run_id, "BI_NP_NONE",
        np_counts={"STANDARD_GROWTH": 5},
        bor_counts={},
        cc_counts={}
    )
    
    # 5. ZERO_TO_ZERO, 6. ZERO_TO_POSITIVE, 7. Num, 8. Invalid excl
    _create_bi(disc_session, run_id, "BI_BOR",
        np_counts={},
        bor_counts={
            "INCREASED": 1,
            "DECREASED": 1,
            "UNCHANGED": 1,
            "ZERO_TO_ZERO": 1, # 90
            "ZERO_TO_POSITIVE": 1, # 15
            "INVALID_NEGATIVE_BORROWINGS": 1, # Excluded
            "UNAVAILABLE": 1 # Excluded
        }, # FB score = (90+15)/2 = 52.5
        cc_counts={}
    )
    
    # 9. Financial N_A
    _create_bi(disc_session, run_id, "BI_FIN",
        np_counts={},
        bor_counts={"ZERO_TO_ZERO": 5},
        cc_counts={},
        std_debt_cnt=0
    )
    
    # 10. CC fallback mappings, 11. CC numeric
    _create_bi(disc_session, run_id, "BI_CC",
        np_counts={},
        bor_counts={},
        cc_counts={
            "STRONG_CASH_CONVERSION": 1,
            "ADEQUATE_CASH_CONVERSION": 1,
            "WEAK_CASH_CONVERSION": 1,
            "PROFIT_WITH_ZERO_OPERATING_CASH_FLOW": 1,
            "NEGATIVE_OPERATING_CASH_FLOW": 1,
            "LOSS_WITH_POSITIVE_OPERATING_CASH_FLOW": 1, # 65
            "LOSS_WITH_ZERO_OPERATING_CASH_FLOW": 1, # 25
            "LOSS_WITH_NEGATIVE_OPERATING_CASH_FLOW": 1, # 5
            "ZERO_PAT_WITH_POSITIVE_OPERATING_CASH_FLOW": 1, # 60
            "ZERO_PAT_WITH_ZERO_OPERATING_CASH_FLOW": 1, # 30
            "ZERO_PAT_WITH_NEGATIVE_OPERATING_CASH_FLOW": 1, # 5
            "UNAVAILABLE": 2 # Excluded
        } # Total fb = (65+25+5+60+30+5)/6 = 190/6 = 31.67
    )
    
    # 13. Empty dist
    _create_bi(disc_session, run_id, "BI_EMPTY", {}, {}, {})
    
    disc_session.commit()
    
    svc = FundamentalBasicIndustryTransitionScoreService(disc_session)
    svc.calculate_basic_industry_transitions(run_id)
    
    def get_t(name):
        g = disc_session.query(GroupScore).filter_by(entity_name=name).first()
        return g.calculation_details["fundamental"]["structural_transition_scores"]
        
    # 1, 2, 3, 12
    t_np = get_t("BI_NP")["net_profit"]
    assert t_np["numeric_status_count"] == 4
    assert t_np["fallback_status_count"] == 8
    assert t_np["valid_status_count"] == 12
    assert t_np["numeric_share_pct"] == 33.33
    assert t_np["fallback_share_pct"] == 66.67
    assert t_np["fallback_score"] == 44.38
    assert t_np["contributions"]["ZERO_BASE_UNCHANGED"]["count"] == 2
    assert t_np["contributions"]["ZERO_BASE_UNCHANGED"]["configured_score"] == 30.0
    
    # 4
    t_np_none = get_t("BI_NP_NONE")["net_profit"]
    assert t_np_none["fallback_score"] is None
    assert t_np_none["available"] is False
    assert t_np_none["reason"] == "NO_STRUCTURAL_FALLBACK_OBSERVATIONS"
    
    # 5, 6, 7, 8
    t_bor = get_t("BI_BOR")["borrowing"]
    assert t_bor["numeric_status_count"] == 3
    assert t_bor["fallback_status_count"] == 2
    assert t_bor["excluded_status_count"] == 2
    assert t_bor["fallback_score"] == 52.5
    
    # 9
    t_fin = get_t("BI_FIN")["borrowing"]
    assert t_fin["applicable"] is False
    assert t_fin["available"] is False
    assert t_fin["fallback_score"] is None
    assert t_fin["reason"] == "N_A_NO_STANDARD_DEBT_RULE_COMPANIES"
    
    # 10, 11
    t_cc = get_t("BI_CC")["cash_conversion"]
    assert t_cc["numeric_status_count"] == 5
    assert t_cc["fallback_status_count"] == 6
    assert t_cc["excluded_status_count"] == 2
    assert t_cc["fallback_score"] == 31.67
    
    # 13
    t_empty = get_t("BI_EMPTY")["net_profit"]
    assert t_empty["valid_status_count"] == 0
    assert t_empty["fallback_score"] is None
    assert t_empty["numeric_share_pct"] is None
    
    # 14, 15
    g = disc_session.query(GroupScore).filter_by(entity_name="BI_NP").first()
    assert g.calculation_details["technical"]["unchanged"] is True
    assert g.calculation_details["macro"]["unchanged"] is True
    assert g.calculation_details["fundamental"]["raw_aggregation"]["unchanged"] is True
    assert g.calculation_details["fundamental"]["metric_normalization"]["unchanged"] is True
    
    # 16
    svc.calculate_basic_industry_transitions(run_id)
    t_np_2 = get_t("BI_NP")["net_profit"]
    assert t_np_2["fallback_score"] == 44.38

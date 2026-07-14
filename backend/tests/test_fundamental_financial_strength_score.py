"""
Tests for FundamentalFinancialStrengthScoreService.
"""
import uuid
import pytest
from sqlalchemy import text

from database import DiscoverySessionLocal
from models.discovery import CompanyFundamentalMetric
from services.fundamental.fundamental_financial_strength_score import FundamentalFinancialStrengthScoreService

@pytest.fixture
def disc_session():
    session = DiscoverySessionLocal()
    session.execute(text("DELETE FROM company_fundamental_metrics"))
    session.commit()
    yield session
    session.execute(text("DELETE FROM company_fundamental_metrics"))
    session.commit()
    session.close()

def _create_company(session, run_id, symbol, std_debt=True, dte_val=None, dte_peer=None, 
                    bt_val=None, bt_peer=None, bt_trans=None):
    calc = {
        "financial_strength": {
            "standard_debt_rule_applicable": std_debt
        },
        "peer_benchmarks": {"metrics": {}},
        "fundamental_scoring": {"growth": {"score": 90.0}, "profitability": {"score": 80.0}},
        "warnings": ["EXISTING_WARN"]
    }
    
    if dte_val is not None:
        calc["financial_strength"]["debt_to_equity"] = dte_val
        calc["financial_strength"]["debt_to_equity_available"] = True
    if dte_peer is not None:
        calc["peer_benchmarks"]["metrics"]["debt_to_equity"] = {
            "available": True,
            "peer_median": dte_peer
        }
        
    if bt_val is not None:
        calc["financial_strength"]["borrowing_change_pct"] = bt_val
        calc["financial_strength"]["borrowing_trend_available"] = True
    if bt_peer is not None:
        calc["peer_benchmarks"]["metrics"]["borrowing_change_pct"] = {
            "available": True,
            "peer_median": bt_peer
        }
    if bt_trans is not None:
        calc["financial_strength"]["borrowing_transition"] = bt_trans
        
    rec = CompanyFundamentalMetric(
        id=str(uuid.uuid4()),
        run_id=run_id,
        source_company_id=symbol,
        symbol=symbol,
        calculation_details=calc
    )
    session.add(rec)

def test_fundamental_financial_strength_score_service(disc_session):
    run_id = "run_fs"
    
    # 1. DTE equal -> 50
    _create_company(disc_session, run_id, "EQ1", dte_val=0.5, dte_peer=0.5)
    
    # 2. DTE 1.0 below -> 100
    _create_company(disc_session, run_id, "HIGH1", dte_val=0.5, dte_peer=1.5)
    
    # 3. DTE 1.0 above -> 0
    _create_company(disc_session, run_id, "LOW1", dte_val=1.5, dte_peer=0.5)
    
    # 4. Scores clamped
    _create_company(disc_session, run_id, "HIGH2", dte_val=0.1, dte_peer=2.0)
    
    # 5. Zero DTE is valid
    _create_company(disc_session, run_id, "ZERO1", dte_val=0.0, dte_peer=0.5) # peer-comp = 0.5. 50 + 0.5*50 = 75
    
    # 6. Borrowing change equal -> 50
    _create_company(disc_session, run_id, "BT_EQ", bt_val=10.0, bt_peer=10.0)
    
    # 7. Borrowing reduction (advantage) scores above peer
    _create_company(disc_session, run_id, "BT_HIGH", bt_val=-20.0, bt_peer=10.0) # advantage 30. 50 + 30 = 80
    
    # 8. Borrowing increase scores below peer
    _create_company(disc_session, run_id, "BT_LOW", bt_val=40.0, bt_peer=10.0) # adv -30. 50 - 30 = 20
    
    # 9, 10, 11. Transitions
    _create_company(disc_session, run_id, "TR_ZZ", bt_trans="ZERO_TO_ZERO") # 90
    _create_company(disc_session, run_id, "TR_ZP", bt_trans="ZERO_TO_POSITIVE") # 15
    
    # 12. Both available
    _create_company(disc_session, run_id, "BOTH", dte_val=0.5, dte_peer=0.5, bt_val=-20.0, bt_peer=10.0)
    # dte_score=50, bt_score=80. 50*60 + 80*40 = 3000 + 3200 / 100 = 62.0 -> NEUTRAL
    
    # 13. Only debt available
    _create_company(disc_session, run_id, "ONLY_DTE", dte_val=0.5, dte_peer=1.5) # 100
    
    # 14. Only borrowing trend
    _create_company(disc_session, run_id, "ONLY_BT", bt_val=10.0, bt_peer=10.0) # 50
    
    # 15. Neither component available
    _create_company(disc_session, run_id, "NONE")
    
    # 16, 17. Bank receives N_A, no partial warning
    _create_company(disc_session, run_id, "BANK", std_debt=False, dte_val=0.5, dte_peer=0.5)
    
    # 18. Boundaries
    _create_company(disc_session, run_id, "ST_VS", dte_val=0.5, dte_peer=1.1) # adv 0.6 -> score 80
    _create_company(disc_session, run_id, "ST_ST", dte_val=0.5, dte_peer=0.9) # adv 0.4 -> score 70
    _create_company(disc_session, run_id, "ST_NE", dte_val=0.5, dte_peer=0.5) # 50
    _create_company(disc_session, run_id, "ST_WK", dte_val=0.7, dte_peer=0.5) # adv -0.2 -> score 40
    _create_company(disc_session, run_id, "ST_VW", dte_val=1.1, dte_peer=0.5) # adv -0.6 -> score 20
    
    disc_session.commit()
    
    # 22. No source DB access
    svc = FundamentalFinancialStrengthScoreService(disc_session)
    svc.score_financial_strength(run_id)
    
    def get_c(sym):
        return disc_session.query(CompanyFundamentalMetric).filter_by(symbol=sym).first()

    def get_fs(sym):
        return get_c(sym).calculation_details["fundamental_scoring"]["financial_strength"]

    # 1.
    fs = get_fs("EQ1")
    assert fs["debt_to_equity"]["score"] == 50.0
    
    # 2.
    assert get_fs("HIGH1")["debt_to_equity"]["score"] == 100.0
    
    # 3.
    assert get_fs("LOW1")["debt_to_equity"]["score"] == 0.0
    
    # 4.
    assert get_fs("HIGH2")["debt_to_equity"]["score"] == 100.0
    
    # 5.
    assert get_fs("ZERO1")["debt_to_equity"]["score"] == 75.0
    
    # 6.
    assert get_fs("BT_EQ")["borrowing_trend"]["score"] == 50.0
    
    # 7.
    assert get_fs("BT_HIGH")["borrowing_trend"]["score"] == 80.0
    
    # 8.
    assert get_fs("BT_LOW")["borrowing_trend"]["score"] == 20.0
    
    # 9, 10, 11
    assert get_fs("TR_ZZ")["borrowing_trend"]["score"] == 90.0
    assert get_fs("TR_ZZ")["borrowing_trend"]["score_source"] == "TRANSITION_STATUS"
    assert "BORROWING_TRANSITION_SCORE_USED" in get_c("TR_ZZ").calculation_details["warnings"]
    assert get_fs("TR_ZP")["borrowing_trend"]["score"] == 15.0
    
    # 12. Both
    fs = get_fs("BOTH")
    assert fs["score"] == 62.0
    assert fs["coverage_pct"] == 100.0
    assert fs["status"] == "NEUTRAL"
    assert get_c("BOTH").financial_strength_score == 62.0
    
    # 13.
    fs = get_fs("ONLY_DTE")
    assert fs["score"] == 100.0
    assert fs["coverage_pct"] == 60.0
    assert "FINANCIAL_STRENGTH_SCORE_PARTIAL" in get_c("ONLY_DTE").calculation_details["warnings"]
    assert "BORROWING_TREND_PEER_BASELINE_UNAVAILABLE" not in get_c("ONLY_DTE").calculation_details["warnings"]
    
    # 14.
    fs = get_fs("ONLY_BT")
    assert fs["score"] == 50.0
    assert fs["coverage_pct"] == 40.0
    assert "DEBT_TO_EQUITY_PEER_BASELINE_UNAVAILABLE" not in get_c("ONLY_BT").calculation_details["warnings"]
    
    # 15. Neither
    fs = get_fs("NONE")
    assert fs["score"] is None
    assert fs["coverage_pct"] == 0.0
    c_none = get_c("NONE")
    assert "FINANCIAL_STRENGTH_SCORE_UNAVAILABLE" in c_none.calculation_details["warnings"]
    assert "FINANCIAL_STRENGTH_SCORE_PARTIAL" not in c_none.calculation_details["warnings"]
    
    # 16, 17. Bank
    fs = get_fs("BANK")
    assert fs["applicable"] is False
    assert fs["score"] is None
    assert fs["coverage_pct"] is None
    assert fs["status"] == "N_A"
    c_bank = get_c("BANK")
    assert "STANDARD_DEBT_RULE_SCORE_NOT_APPLICABLE" in c_bank.calculation_details["warnings"]
    assert "FINANCIAL_STRENGTH_SCORE_UNAVAILABLE" not in c_bank.calculation_details["warnings"]
    assert "FINANCIAL_STRENGTH_SCORE_PARTIAL" not in c_bank.calculation_details["warnings"]
    
    # 18. Boundaries
    assert get_fs("ST_VS")["status"] == "VERY_STRONG"
    assert get_fs("ST_ST")["status"] == "STRONG"
    assert get_fs("ST_NE")["status"] == "NEUTRAL"
    assert get_fs("ST_WK")["status"] == "WEAK"
    assert get_fs("ST_VW")["status"] == "VERY_WEAK"
    
    # 19, 20. Existing JSON
    c_eq = get_c("EQ1")
    assert c_eq.calculation_details["fundamental_scoring"]["growth"]["score"] == 90.0
    assert c_eq.calculation_details["fundamental_scoring"]["profitability"]["score"] == 80.0
    
    # 21. Idempotent
    svc.score_financial_strength(run_id)
    c_eq_2 = get_c("EQ1")
    assert c_eq_2.calculation_details["fundamental_scoring"]["financial_strength"]["score"] == 50.0

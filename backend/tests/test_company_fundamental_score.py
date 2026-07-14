"""
Tests for CompanyFundamentalScoreService.
"""
import uuid
import pytest
from sqlalchemy import text

from database import DiscoverySessionLocal
from models.discovery import CompanyFundamentalMetric
from services.fundamental.company_fundamental_score import CompanyFundamentalScoreService

@pytest.fixture
def disc_session():
    session = DiscoverySessionLocal()
    session.execute(text("DELETE FROM company_fundamental_metrics"))
    session.commit()
    yield session
    session.execute(text("DELETE FROM company_fundamental_metrics"))
    session.commit()
    session.close()

def _create_company(session, run_id, symbol,
                    g_score=None, p_score=None, fs_score=None, eq_score=None,
                    fs_app=True):
    calc = {
        "fundamental_scoring": {},
        "peer_benchmarks": {"metrics": {}},
        "warnings": ["EXISTING_WARN"]
    }
    
    if g_score is not None:
        calc["fundamental_scoring"]["growth"] = {"score": g_score}
    if p_score is not None:
        calc["fundamental_scoring"]["profitability"] = {"score": p_score}
    
    calc["fundamental_scoring"]["financial_strength"] = {"applicable": fs_app}
    if fs_score is not None:
        calc["fundamental_scoring"]["financial_strength"]["score"] = fs_score
        
    if eq_score is not None:
        calc["fundamental_scoring"]["earnings_quality"] = {"score": eq_score}
        
    rec = CompanyFundamentalMetric(
        id=str(uuid.uuid4()),
        run_id=run_id,
        source_company_id=symbol,
        symbol=symbol,
        calculation_details=calc
    )
    session.add(rec)

def test_company_fundamental_score_service(disc_session):
    run_id = "run_fin"
    
    # 1. All four components available. (2. Equal 25% weighting)
    # (80*25 + 70*25 + 60*25 + 90*25) / 100 = 75.0
    _create_company(disc_session, run_id, "ALL_4", g_score=80.0, p_score=70.0, fs_score=60.0, eq_score=90.0)
    
    # 3. One applicable missing, 6. Standard coverage (75%)
    # (80*25 + 70*25 + 60*25) / 75 = 70.0
    _create_company(disc_session, run_id, "MISS_1", g_score=80.0, p_score=70.0, eq_score=60.0)
    
    # 4. Two missing, 13. Low coverage preserves score (50%)
    # (80*25 + 70*25) / 50 = 75.0
    _create_company(disc_session, run_id, "MISS_2", g_score=80.0, p_score=70.0)
    
    # 5. No component available
    _create_company(disc_session, run_id, "NONE")
    
    # 7. Financial business excludes fs from denominator (8, 10)
    # (80*25 + 70*25 + 60*25) / 75 = 70.0
    _create_company(disc_session, run_id, "FIN_ALL_3", g_score=80.0, p_score=70.0, eq_score=60.0, fs_app=False)
    
    # 9. Financial business missing one applicable score has 66.67% coverage
    _create_company(disc_session, run_id, "FIN_MISS_1", g_score=80.0, p_score=70.0, fs_app=False)
    
    # 11. Coverage exactly 75% remains eligible
    _create_company(disc_session, run_id, "COV_75", g_score=80.0, p_score=70.0, eq_score=60.0)
    
    # 12. Coverage below 75% becomes ineligible
    _create_company(disc_session, run_id, "COV_50", g_score=80.0, p_score=70.0)
    
    # 14. Boundaries (80-100: VERY_STRONG, 65-79.9: STRONG, 50-64.9: NEUTRAL, 35-49.9: WEAK, 0-34.9: VERY_WEAK)
    _create_company(disc_session, run_id, "ST_VS", g_score=100.0, p_score=100.0, fs_score=100.0, eq_score=100.0) # 100
    _create_company(disc_session, run_id, "ST_ST", g_score=75.0, p_score=75.0, fs_score=75.0, eq_score=75.0) # 75
    _create_company(disc_session, run_id, "ST_NE", g_score=50.0, p_score=50.0, fs_score=50.0, eq_score=50.0) # 50
    _create_company(disc_session, run_id, "ST_WK", g_score=40.0, p_score=40.0, fs_score=40.0, eq_score=40.0) # 40
    _create_company(disc_session, run_id, "ST_VW", g_score=20.0, p_score=20.0, fs_score=20.0, eq_score=20.0) # 20
    
    # 15. Non-finite
    _create_company(disc_session, run_id, "NON_FINITE", g_score=80.0, p_score="inf", fs_score="nan", eq_score=70.0)
    
    disc_session.commit()
    
    svc = CompanyFundamentalScoreService(disc_session)
    svc.score_companies(run_id)
    
    def get_c(sym):
        return disc_session.query(CompanyFundamentalMetric).filter_by(symbol=sym).first()

    def get_f(sym):
        return get_c(sym).calculation_details["fundamental_scoring"]["final"]

    # 1, 2.
    f = get_f("ALL_4")
    assert f["score"] == 75.0
    assert f["applicable_weight"] == 100.0
    assert f["available_weight"] == 100.0
    assert f["coverage_pct"] == 100.0
    assert f["eligible_for_selection"] is True
    
    # 3, 6.
    f = get_f("MISS_1")
    assert f["score"] == 70.0
    assert f["applicable_weight"] == 100.0
    assert f["available_weight"] == 75.0
    assert f["coverage_pct"] == 75.0
    assert f["eligible_for_selection"] is True
    c = get_c("MISS_1")
    assert "FUNDAMENTAL_SCORE_PARTIAL" in c.calculation_details["warnings"]
    assert "FUNDAMENTAL_SCORE_LOW_COVERAGE" not in c.calculation_details["warnings"]
    
    # 4, 13.
    f = get_f("MISS_2")
    assert f["score"] == 75.0
    assert f["applicable_weight"] == 100.0
    assert f["available_weight"] == 50.0
    assert f["coverage_pct"] == 50.0
    assert f["eligible_for_selection"] is False
    c = get_c("MISS_2")
    assert "FUNDAMENTAL_SCORE_LOW_COVERAGE" in c.calculation_details["warnings"]
    assert "FUNDAMENTAL_SCORE_PARTIAL" in c.calculation_details["warnings"]
    
    # 5.
    f = get_f("NONE")
    assert f["score"] is None
    assert f["available_weight"] == 0.0
    assert f["coverage_pct"] == 0.0
    assert f["status"] == "UNAVAILABLE"
    assert f["eligible_for_selection"] is False
    assert "FUNDAMENTAL_SCORE_UNAVAILABLE" in get_c("NONE").calculation_details["warnings"]
    
    # 7, 8, 10.
    f = get_f("FIN_ALL_3")
    assert f["score"] == 70.0
    assert f["applicable_weight"] == 75.0
    assert f["available_weight"] == 75.0
    assert f["coverage_pct"] == 100.0
    assert f["eligible_for_selection"] is True
    c = get_c("FIN_ALL_3")
    assert "FUNDAMENTAL_SCORE_PARTIAL" not in c.calculation_details["warnings"]
    assert "FUNDAMENTAL_SCORE_LOW_COVERAGE" not in c.calculation_details["warnings"]
    assert f["components"]["financial_strength"]["applicable"] is False
    assert f["components"]["financial_strength"]["reason"] == "N_A_STANDARD_DEBT_RULE"
    
    # 9.
    f = get_f("FIN_MISS_1")
    assert f["score"] == 75.0
    assert f["applicable_weight"] == 75.0
    assert f["available_weight"] == 50.0
    assert round(f["coverage_pct"], 2) == 66.67
    assert f["eligible_for_selection"] is False
    assert "FUNDAMENTAL_SCORE_PARTIAL" in get_c("FIN_MISS_1").calculation_details["warnings"]
    assert "FUNDAMENTAL_SCORE_LOW_COVERAGE" in get_c("FIN_MISS_1").calculation_details["warnings"]
    
    # 11.
    assert get_f("COV_75")["eligible_for_selection"] is True
    
    # 12.
    assert get_f("COV_50")["eligible_for_selection"] is False
    
    # 14.
    assert get_f("ST_VS")["status"] == "VERY_STRONG"
    assert get_f("ST_ST")["status"] == "STRONG"
    assert get_f("ST_NE")["status"] == "NEUTRAL"
    assert get_f("ST_WK")["status"] == "WEAK"
    assert get_f("ST_VW")["status"] == "VERY_WEAK"
    
    # 15.
    f = get_f("NON_FINITE")
    assert f["available_weight"] == 50.0 # g and eq
    assert f["score"] == 75.0 # (80*25 + 70*25) / 50
    assert f["components"]["profitability"]["available"] is False
    assert f["components"]["financial_strength"]["available"] is False
    
    # 16, 17.
    c = get_c("ALL_4")
    assert c.calculation_details["fundamental_scoring"]["growth"]["score"] == 80.0
    assert c.final_fundamental_score == 75.0
    assert c.fundamental_status == "STRONG"
    assert c.fundamental_eligible_for_selection is True
    
    # 19.
    svc.score_companies(run_id)
    c2 = get_c("ALL_4")
    assert c2.final_fundamental_score == 75.0

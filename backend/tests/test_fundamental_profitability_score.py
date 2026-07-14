"""
Tests for FundamentalProfitabilityScoreService.
"""
import uuid
import pytest
from sqlalchemy import text

from database import DiscoverySessionLocal
from models.discovery import CompanyFundamentalMetric
from services.fundamental.fundamental_profitability_score import FundamentalProfitabilityScoreService

@pytest.fixture
def disc_session():
    session = DiscoverySessionLocal()
    session.execute(text("DELETE FROM company_fundamental_metrics"))
    session.commit()
    yield session
    session.execute(text("DELETE FROM company_fundamental_metrics"))
    session.commit()
    session.close()

def _create_company(session, run_id, symbol, om_val=None, om_peer=None, 
                    mt_val=None, mt_peer=None):
    calc = {
        "profitability": {},
        "peer_benchmarks": {"metrics": {}},
        "fundamental_scoring": {"growth": {"score": 90.0}},
        "warnings": ["EXISTING_WARN"]
    }
    
    if om_val is not None:
        calc["profitability"]["latest_operating_margin_pct"] = om_val
        calc["profitability"]["latest_operating_margin_available"] = True
    if om_peer is not None:
        calc["peer_benchmarks"]["metrics"]["latest_operating_margin_pct"] = {
            "available": True,
            "peer_median": om_peer
        }
        
    if mt_val is not None:
        calc["profitability"]["operating_margin_change_pp"] = mt_val
        calc["profitability"]["operating_margin_trend_available"] = True
    if mt_peer is not None:
        calc["peer_benchmarks"]["metrics"]["operating_margin_change_pp"] = {
            "available": True,
            "peer_median": mt_peer
        }
        
    rec = CompanyFundamentalMetric(
        id=str(uuid.uuid4()),
        run_id=run_id,
        source_company_id=symbol,
        symbol=symbol,
        calculation_details=calc
    )
    session.add(rec)

def test_fundamental_profitability_score_service(disc_session):
    run_id = "run_prof"
    
    # 1. Equal to peer scores 50
    _create_company(disc_session, run_id, "EQ1", om_val=15.0, om_peer=15.0)
    
    # 2. 10 points above -> 100
    _create_company(disc_session, run_id, "HIGH1", om_val=25.0, om_peer=15.0)
    
    # 3. Beyond upper clamped
    _create_company(disc_session, run_id, "HIGH2", om_val=30.0, om_peer=15.0)
    
    # 4. 10 points below -> 0
    _create_company(disc_session, run_id, "LOW1", om_val=5.0, om_peer=15.0)
    
    # 5. Negative company and peer margins
    _create_company(disc_session, run_id, "NEG1", om_val=-5.0, om_peer=-15.0) # +10 points -> 100
    
    # 6. Trend equal -> 50, 7. Trend above -> 100, below -> 0
    _create_company(disc_session, run_id, "TR_EQ", mt_val=2.0, mt_peer=2.0)
    _create_company(disc_session, run_id, "TR_HIGH", mt_val=7.0, mt_peer=2.0)
    _create_company(disc_session, run_id, "TR_LOW", mt_val=-3.0, mt_peer=2.0)
    
    # 8. Missing margin peer, 12. Only trend available
    _create_company(disc_session, run_id, "MISS_OM", om_val=10.0, mt_val=2.0, mt_peer=2.0)
    
    # 9. Missing trend peer, 11. Only margin available
    _create_company(disc_session, run_id, "MISS_MT", om_val=20.0, om_peer=10.0, mt_val=2.0)
    
    # 10. Both available
    _create_company(disc_session, run_id, "BOTH", om_val=15.0, om_peer=15.0, mt_val=7.0, mt_peer=2.0)
    # om_score = 50, mt_score = 100.
    # total = (50*60 + 100*40) / 100 = 3000 + 4000 / 100 = 70.0 -> STRONG
    
    # 13. Neither available
    _create_company(disc_session, run_id, "NONE")
    
    # 15. Every status boundary (80-100: VERY_STRONG, 65-79.9: STRONG, 50-64.9: NEUTRAL, 35-49.9: WEAK, 0-34.9: VERY_WEAK)
    _create_company(disc_session, run_id, "ST_VS", om_val=25.0, om_peer=15.0) # 100
    _create_company(disc_session, run_id, "ST_ST", om_val=20.0, om_peer=15.0) # 75 -> STRONG
    _create_company(disc_session, run_id, "ST_NE", om_val=15.0, om_peer=15.0) # 50 -> NEUTRAL
    _create_company(disc_session, run_id, "ST_WK", om_val=10.0, om_peer=15.0) # 25 -> VERY_WEAK
    # For WEAK (35-49.9): say score 40 -> delta is -2.0 (50 + (-2/10)*50 = 40)
    _create_company(disc_session, run_id, "ST_WK2", om_val=13.0, om_peer=15.0) # 40 -> WEAK
    
    disc_session.commit()
    
    # 19. No source DB access
    svc = FundamentalProfitabilityScoreService(disc_session)
    svc.score_profitability(run_id)
    
    def get_c(sym):
        return disc_session.query(CompanyFundamentalMetric).filter_by(symbol=sym).first()

    def get_p(sym):
        return get_c(sym).calculation_details["fundamental_scoring"]["profitability"]

    # 1.
    p = get_p("EQ1")
    assert p["operating_margin"]["score"] == 50.0
    
    # 2.
    p = get_p("HIGH1")
    assert p["operating_margin"]["score"] == 100.0
    
    # 3.
    p = get_p("HIGH2")
    assert p["operating_margin"]["score"] == 100.0
    
    # 4.
    p = get_p("LOW1")
    assert p["operating_margin"]["score"] == 0.0
    
    # 5.
    p = get_p("NEG1")
    assert p["operating_margin"]["score"] == 100.0
    
    # 6, 7.
    assert get_p("TR_EQ")["margin_trend"]["score"] == 50.0
    assert get_p("TR_HIGH")["margin_trend"]["score"] == 100.0
    assert get_p("TR_LOW")["margin_trend"]["score"] == 0.0
    
    # 8, 12. Only trend
    p = get_p("MISS_OM")
    assert p["operating_margin"]["available"] is False
    assert "OPERATING_MARGIN_PEER_BASELINE_UNAVAILABLE" in get_c("MISS_OM").calculation_details["warnings"]
    assert p["coverage_pct"] == 40.0
    assert p["score"] == 50.0 # Trend score is 50, renormalized
    
    # 9, 11. Only margin
    p = get_p("MISS_MT")
    assert p["margin_trend"]["available"] is False
    assert "MARGIN_TREND_PEER_BASELINE_UNAVAILABLE" in get_c("MISS_MT").calculation_details["warnings"]
    assert p["coverage_pct"] == 60.0
    assert p["score"] == 100.0
    
    # 10. Both
    p = get_p("BOTH")
    assert p["score"] == 70.0
    assert p["coverage_pct"] == 100.0
    assert get_c("BOTH").profitability_score == 70.0
    
    # 13, 14. Neither and warnings
    p = get_p("NONE")
    assert p["score"] is None
    assert p["coverage_pct"] == 0.0
    c_none = get_c("NONE")
    assert "PROFITABILITY_SCORE_UNAVAILABLE" in c_none.calculation_details["warnings"]
    assert "PROFITABILITY_SCORE_PARTIAL" not in c_none.calculation_details["warnings"]
    
    c_part = get_c("MISS_OM")
    assert "PROFITABILITY_SCORE_PARTIAL" in c_part.calculation_details["warnings"]
    
    # 15. Every boundary
    assert get_p("ST_VS")["status"] == "VERY_STRONG"
    assert get_p("ST_ST")["status"] == "STRONG"
    assert get_p("ST_NE")["status"] == "NEUTRAL"
    assert get_p("ST_WK2")["status"] == "WEAK"
    assert get_p("ST_WK")["status"] == "VERY_WEAK"
    
    # 16, 17. Existing unchanged
    c_eq = get_c("EQ1")
    assert c_eq.calculation_details["fundamental_scoring"]["growth"]["score"] == 90.0
    assert c_eq.calculation_details["profitability"]["latest_operating_margin_pct"] == 15.0
    
    # 18. Idempotent
    svc.score_profitability(run_id)
    c_eq_2 = get_c("EQ1")
    assert c_eq_2.calculation_details["fundamental_scoring"]["profitability"]["score"] == 50.0

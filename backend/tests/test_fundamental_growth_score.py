"""
Tests for FundamentalGrowthScoreService.
"""
import uuid
import pytest
from sqlalchemy import text
import copy

from database import DiscoverySessionLocal
from models.discovery import CompanyFundamentalMetric
from services.fundamental.fundamental_growth_score import FundamentalGrowthScoreService

@pytest.fixture
def disc_session():
    session = DiscoverySessionLocal()
    session.execute(text("DELETE FROM company_fundamental_metrics"))
    session.commit()
    yield session
    session.execute(text("DELETE FROM company_fundamental_metrics"))
    session.commit()
    session.close()

def _create_company(session, run_id, symbol, sales_val=None, sales_peer=None, 
                    np_val=None, np_peer=None, np_trans=None):
    calc = {
        "growth": {},
        "peer_benchmarks": {"metrics": {}},
        "warnings": ["EXISTING_WARN"]
    }
    
    if sales_val is not None:
        calc["growth"]["sales_growth_pct"] = sales_val
        calc["growth"]["sales_growth_available"] = True
    if sales_peer is not None:
        calc["peer_benchmarks"]["metrics"]["sales_growth_pct"] = {
            "available": True,
            "peer_median": sales_peer
        }
        
    if np_val is not None:
        calc["growth"]["net_profit_growth_pct"] = np_val
        calc["growth"]["net_profit_growth_available"] = True
    if np_peer is not None:
        calc["peer_benchmarks"]["metrics"]["net_profit_growth_pct"] = {
            "available": True,
            "peer_median": np_peer
        }
    if np_trans is not None:
        calc["growth"]["net_profit_transition_status"] = np_trans
        
    rec = CompanyFundamentalMetric(
        id=str(uuid.uuid4()),
        run_id=run_id,
        source_company_id=symbol,
        symbol=symbol,
        calculation_details=calc
    )
    session.add(rec)

def test_fundamental_growth_score_service(disc_session):
    run_id = "run_growth"
    
    # 1. Equal to median -> 50
    _create_company(disc_session, run_id, "EQ1", sales_val=10.0, sales_peer=10.0)
    
    # 2. 20 points above -> 100
    _create_company(disc_session, run_id, "HIGH1", sales_val=30.0, sales_peer=10.0)
    
    # 3. More than 20 points above -> clamped to 100
    _create_company(disc_session, run_id, "HIGH2", sales_val=40.0, sales_peer=10.0)
    
    # 4. 20 points below -> 0
    _create_company(disc_session, run_id, "LOW1", sales_val=-10.0, sales_peer=10.0)
    
    # 5. Negative company and peer growth values
    _create_company(disc_session, run_id, "NEG1", sales_val=-20.0, sales_peer=-30.0) # +10 points -> 75
    
    # 6. Missing sales peer baseline -> unavailable metric, warning added
    _create_company(disc_session, run_id, "MISS1", sales_val=10.0) 
    
    # 7. Standard net-profit peer scoring
    _create_company(disc_session, run_id, "NP1", np_val=20.0, np_peer=20.0) # 50
    
    # 8. Every non-standard PAT transition score + 9. No peer median required
    _create_company(disc_session, run_id, "TR_LP", np_trans="LOSS_TO_PROFIT") # 90
    _create_company(disc_session, run_id, "TR_LN", np_trans="LOSS_NARROWED") # 65
    _create_company(disc_session, run_id, "TR_LU", np_trans="LOSS_UNCHANGED") # 35
    _create_company(disc_session, run_id, "TR_LW", np_trans="LOSS_WIDENED") # 10
    _create_company(disc_session, run_id, "TR_ZP", np_trans="ZERO_BASE_TO_PROFIT") # 85
    _create_company(disc_session, run_id, "TR_ZL", np_trans="ZERO_BASE_TO_LOSS") # 10
    _create_company(disc_session, run_id, "TR_ZU", np_trans="ZERO_BASE_UNCHANGED") # 30
    
    # 10. Both metrics available
    _create_company(disc_session, run_id, "BOTH1", sales_val=20.0, sales_peer=10.0, np_val=10.0, np_peer=10.0) # 75 and 50 -> 62.5
    
    # 12. Neither metric available
    _create_company(disc_session, run_id, "NONE1")
    
    disc_session.commit()
    
    # 16. No DB access inside calculation
    svc = FundamentalGrowthScoreService(disc_session)
    svc.score_growth(run_id)
    
    def get_c(sym):
        return disc_session.query(CompanyFundamentalMetric).filter_by(symbol=sym).first()

    def get_g(sym):
        return get_c(sym).calculation_details["fundamental_scoring"]["growth"]

    # 1. Equal to median
    g = get_g("EQ1")
    assert g["sales_growth"]["score"] == 50.0
    
    # 2. 20 points above
    g = get_g("HIGH1")
    assert g["sales_growth"]["score"] == 100.0
    
    # 3. Clamped
    g = get_g("HIGH2")
    assert g["sales_growth"]["score"] == 100.0
    
    # 4. 20 below
    g = get_g("LOW1")
    assert g["sales_growth"]["score"] == 0.0
    
    # 5. Negative
    g = get_g("NEG1")
    assert g["sales_growth"]["score"] == 75.0
    
    # 6. Missing peer baseline + 11. One metric available re-normalization
    c = get_c("MISS1")
    g = c.calculation_details["fundamental_scoring"]["growth"]
    assert g["sales_growth"]["available"] is False
    assert "SALES_PEER_BASELINE_UNAVAILABLE" in c.calculation_details["warnings"]
    assert g["coverage_pct"] == 0.0
    
    # 7. Standard NP
    g = get_g("NP1")
    assert g["net_profit_growth"]["score"] == 50.0
    assert g["net_profit_growth"]["score_source"] == "PEER_RELATIVE_NUMERIC"
    
    # 8. Transitions
    assert get_g("TR_LP")["net_profit_growth"]["score"] == 90.0
    assert get_g("TR_LN")["net_profit_growth"]["score"] == 65.0
    assert get_g("TR_LU")["net_profit_growth"]["score"] == 35.0
    assert get_g("TR_LW")["net_profit_growth"]["score"] == 10.0
    assert get_g("TR_ZP")["net_profit_growth"]["score"] == 85.0
    assert get_g("TR_ZL")["net_profit_growth"]["score"] == 10.0
    assert get_g("TR_ZU")["net_profit_growth"]["score"] == 30.0
    
    # 9. No peer median needed
    assert get_g("TR_LP")["net_profit_growth"]["score_source"] == "TRANSITION_STATUS"
    c_tr = get_c("TR_LP")
    assert "NET_PROFIT_TRANSITION_SCORE_USED" in c_tr.calculation_details["warnings"]
    
    # 10. Both metrics
    g = get_g("BOTH1")
    assert g["score"] == 62.5 # (75*50 + 50*50)/100
    assert g["coverage_pct"] == 100.0
    assert get_c("BOTH1").growth_score == 62.5
    
    # 12. Neither
    g = get_g("NONE1")
    assert g["score"] is None
    assert g["status"] == "UNAVAILABLE"
    assert g["coverage_pct"] == 0.0
    assert "GROWTH_SCORE_UNAVAILABLE" in get_c("NONE1").calculation_details["warnings"]
    
    # 13. Growth status boundaries
    assert get_g("HIGH1")["status"] == "VERY_STRONG" # 100
    assert get_g("TR_LP")["status"] == "VERY_STRONG" # 90
    assert get_g("NEG1")["status"] == "STRONG" # 75
    assert get_g("BOTH1")["status"] == "NEUTRAL" # 62.5
    assert get_g("TR_LU")["status"] == "WEAK" # 35
    assert get_g("TR_LW")["status"] == "VERY_WEAK" # 10
    
    # 14. JSON untouched
    c_eq = get_c("EQ1")
    assert c_eq.calculation_details["growth"]["sales_growth_pct"] == 10.0
    
    # 15. Idempotent
    svc.score_growth(run_id)
    c_eq_2 = get_c("EQ1")
    assert c_eq_2.calculation_details["fundamental_scoring"]["growth"]["score"] == 50.0

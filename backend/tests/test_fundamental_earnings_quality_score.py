"""
Tests for FundamentalEarningsQualityScoreService.
"""
import uuid
import pytest
from sqlalchemy import text

from database import DiscoverySessionLocal
from models.discovery import CompanyFundamentalMetric
from services.fundamental.fundamental_earnings_quality_score import FundamentalEarningsQualityScoreService

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
                    cc_val=None, cc_peer=None, cc_status=None,
                    cct_val=None, cct_peer=None,
                    ph_val=None,
                    vol_val=None, vol_peer=None):
    calc = {
        "earnings_quality": {
            "cash_conversion": {},
            "profit_stability": {}
        },
        "peer_benchmarks": {"metrics": {}},
        "fundamental_scoring": {
            "growth": {"score": 90.0}, 
            "profitability": {"score": 80.0},
            "financial_strength": {"score": 70.0}
        },
        "warnings": ["EXISTING_WARN"]
    }
    
    if cc_val is not None:
        calc["earnings_quality"]["cash_conversion"]["latest_ocf_to_pat"] = cc_val
        calc["earnings_quality"]["cash_conversion"]["latest_ocf_to_pat_available"] = True
    if cc_peer is not None:
        calc["peer_benchmarks"]["metrics"]["latest_ocf_to_pat"] = {
            "available": True,
            "peer_median": cc_peer
        }
    if cc_status is not None:
        calc["earnings_quality"]["cash_conversion"]["latest_cash_conversion_status"] = cc_status
        
    if cct_val is not None:
        calc["earnings_quality"]["cash_conversion"]["ocf_to_pat_change"] = cct_val
        calc["earnings_quality"]["cash_conversion"]["ocf_to_pat_change_available"] = True
    if cct_peer is not None:
        calc["peer_benchmarks"]["metrics"]["ocf_to_pat_change"] = {
            "available": True,
            "peer_median": cct_peer
        }

    if ph_val is not None:
        calc["earnings_quality"]["profit_stability"]["positive_pat_period_ratio"] = ph_val
        calc["earnings_quality"]["profit_stability"]["profit_stability_available"] = True

    if vol_val is not None:
        calc["earnings_quality"]["profit_stability"]["pat_growth_volatility_pct"] = vol_val
        calc["earnings_quality"]["profit_stability"]["pat_growth_volatility_available"] = True
    if vol_peer is not None:
        calc["peer_benchmarks"]["metrics"]["pat_growth_volatility_pct"] = {
            "available": True,
            "peer_median": vol_peer
        }
        
    rec = CompanyFundamentalMetric(
        id=str(uuid.uuid4()),
        run_id=run_id,
        source_company_id=symbol,
        symbol=symbol,
        calculation_details=calc
    )
    session.add(rec)

def test_fundamental_earnings_quality_score_service(disc_session):
    run_id = "run_eq"
    
    # 1. CC equal -> 50
    _create_company(disc_session, run_id, "CC_EQ", cc_val=1.2, cc_peer=1.2)
    
    # 2. CC 1.0 above -> 100
    _create_company(disc_session, run_id, "CC_HIGH", cc_val=2.2, cc_peer=1.2)
    
    # 3. CC 1.0 below -> 0
    _create_company(disc_session, run_id, "CC_LOW", cc_val=0.2, cc_peer=1.2)
    
    # 4. Clamped
    _create_company(disc_session, run_id, "CC_CLAMP", cc_val=3.0, cc_peer=1.0)
    
    # 5, 6. Every fallback
    _create_company(disc_session, run_id, "F_L_P", cc_status="LOSS_WITH_POSITIVE_OPERATING_CASH_FLOW") # 65
    _create_company(disc_session, run_id, "F_L_Z", cc_status="LOSS_WITH_ZERO_OPERATING_CASH_FLOW") # 25
    _create_company(disc_session, run_id, "F_L_N", cc_status="LOSS_WITH_NEGATIVE_OPERATING_CASH_FLOW") # 5
    _create_company(disc_session, run_id, "F_Z_P", cc_status="ZERO_PAT_WITH_POSITIVE_OPERATING_CASH_FLOW") # 60
    _create_company(disc_session, run_id, "F_Z_Z", cc_status="ZERO_PAT_WITH_ZERO_OPERATING_CASH_FLOW") # 30
    _create_company(disc_session, run_id, "F_Z_N", cc_status="ZERO_PAT_WITH_NEGATIVE_OPERATING_CASH_FLOW") # 5
    _create_company(disc_session, run_id, "F_P_Z", cc_status="PROFIT_WITH_ZERO_OPERATING_CASH_FLOW") # 15
    _create_company(disc_session, run_id, "F_NEG", cc_status="NEGATIVE_OPERATING_CASH_FLOW") # 0
    
    # 7. Missing PAT/OCF -> no fallback
    _create_company(disc_session, run_id, "F_NONE", cc_status=None)
    
    # 8. CCT peer scoring
    _create_company(disc_session, run_id, "CCT_OK", cct_val=0.3, cct_peer=0.1) # delta 0.2. 50 + (0.2/0.5)*50 = 70.0
    
    # 9, 10. PAT ratio
    _create_company(disc_session, run_id, "PH_OK", ph_val=80.0)
    _create_company(disc_session, run_id, "PH_NONE")
    
    # 11, 12, 13, 14. Volatility
    _create_company(disc_session, run_id, "VOL_EQ", vol_val=30.0, vol_peer=30.0) # 50
    _create_company(disc_session, run_id, "VOL_LOW", vol_val=10.0, vol_peer=30.0) # adv 20 -> 50 + (20/50)*50 = 70
    _create_company(disc_session, run_id, "VOL_HIGH", vol_val=60.0, vol_peer=30.0) # adv -30 -> 20
    _create_company(disc_session, run_id, "VOL_ZERO", vol_val=0.0, vol_peer=30.0) # adv 30 -> 80
    
    # 15. All four available
    _create_company(disc_session, run_id, "ALL_4", 
                    cc_val=1.2, cc_peer=0.8, # delta 0.4 -> 70. (w=40)
                    cct_val=0.3, cct_peer=0.1, # delta 0.2 -> 70. (w=20)
                    ph_val=80.0, # 80. (w=25)
                    vol_val=20.0, vol_peer=40.0) # adv 20 -> 70. (w=15)
    # (70*40 + 70*20 + 80*25 + 70*15) / 100 = (2800 + 1400 + 2000 + 1050) / 100 = 7250 / 100 = 72.5 -> STRONG
    
    # 16, 18. Partial component weight normalization
    _create_company(disc_session, run_id, "PARTIAL", ph_val=80.0)
    
    # 17. No components
    _create_company(disc_session, run_id, "NONE")
    
    # 19. Boundaries
    _create_company(disc_session, run_id, "ST_VS", ph_val=100.0) # 100 (VERY_STRONG)
    _create_company(disc_session, run_id, "ST_ST", ph_val=75.0) # 75 (STRONG)
    _create_company(disc_session, run_id, "ST_NE", ph_val=50.0) # 50 (NEUTRAL)
    _create_company(disc_session, run_id, "ST_WK", ph_val=40.0) # 40 (WEAK)
    _create_company(disc_session, run_id, "ST_VW", ph_val=20.0) # 20 (VERY_WEAK)
    
    disc_session.commit()
    
    svc = FundamentalEarningsQualityScoreService(disc_session)
    svc.score_earnings_quality(run_id)
    
    def get_c(sym):
        return disc_session.query(CompanyFundamentalMetric).filter_by(symbol=sym).first()

    def get_eq(sym):
        return get_c(sym).calculation_details["fundamental_scoring"]["earnings_quality"]

    assert get_eq("CC_EQ")["latest_cash_conversion"]["score"] == 50.0
    assert get_eq("CC_HIGH")["latest_cash_conversion"]["score"] == 100.0
    assert get_eq("CC_LOW")["latest_cash_conversion"]["score"] == 0.0
    assert get_eq("CC_CLAMP")["latest_cash_conversion"]["score"] == 100.0
    
    assert get_eq("F_L_P")["latest_cash_conversion"]["score"] == 65.0
    assert get_eq("F_L_P")["latest_cash_conversion"]["score_source"] == "STATUS_FALLBACK"
    assert "CASH_CONVERSION_STATUS_SCORE_USED" in get_c("F_L_P").calculation_details["warnings"]
    assert get_eq("F_L_Z")["latest_cash_conversion"]["score"] == 25.0
    assert get_eq("F_NEG")["latest_cash_conversion"]["score"] == 0.0
    
    assert get_eq("F_NONE")["latest_cash_conversion"]["score"] is None
    
    assert get_eq("CCT_OK")["cash_conversion_trend"]["score"] == 70.0
    
    assert get_eq("PH_OK")["profit_history"]["score"] == 80.0
    
    assert get_eq("VOL_EQ")["pat_growth_volatility"]["score"] == 50.0
    assert get_eq("VOL_LOW")["pat_growth_volatility"]["score"] == 70.0
    assert get_eq("VOL_HIGH")["pat_growth_volatility"]["score"] == 20.0
    assert get_eq("VOL_ZERO")["pat_growth_volatility"]["score"] == 80.0
    
    # 15.
    eq = get_eq("ALL_4")
    assert eq["score"] == 72.5
    assert eq["coverage_pct"] == 100.0
    assert eq["status"] == "STRONG"
    
    # 16.
    eq = get_eq("PARTIAL")
    assert eq["score"] == 80.0
    assert eq["coverage_pct"] == 25.0
    assert "EARNINGS_QUALITY_SCORE_PARTIAL" in get_c("PARTIAL").calculation_details["warnings"]
    
    # 17.
    eq = get_eq("NONE")
    assert eq["score"] is None
    assert eq["coverage_pct"] == 0.0
    assert "EARNINGS_QUALITY_SCORE_UNAVAILABLE" in get_c("NONE").calculation_details["warnings"]
    
    # 19.
    assert get_eq("ST_VS")["status"] == "VERY_STRONG"
    assert get_eq("ST_ST")["status"] == "STRONG"
    assert get_eq("ST_NE")["status"] == "NEUTRAL"
    assert get_eq("ST_WK")["status"] == "WEAK"
    assert get_eq("ST_VW")["status"] == "VERY_WEAK"
    
    # 20.
    c = get_c("CC_EQ")
    assert c.calculation_details["fundamental_scoring"]["growth"]["score"] == 90.0
    assert c.calculation_details["fundamental_scoring"]["profitability"]["score"] == 80.0
    assert c.calculation_details["fundamental_scoring"]["financial_strength"]["score"] == 70.0
    
    # 22.
    svc.score_earnings_quality(run_id)
    c_eq_2 = get_c("CC_EQ")
    assert c_eq_2.calculation_details["fundamental_scoring"]["earnings_quality"]["score"] == 50.0

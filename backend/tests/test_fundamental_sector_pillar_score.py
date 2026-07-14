"""
Tests for FundamentalSectorPillarScoreService.
"""
import uuid
import pytest
from sqlalchemy import text

from database import DiscoverySessionLocal
from models.discovery import GroupScore
from services.fundamental.fundamental_sector_pillar_score import FundamentalSectorPillarScoreService

@pytest.fixture
def disc_session():
    session = DiscoverySessionLocal()
    session.execute(text("DELETE FROM group_scores"))
    session.commit()
    yield session
    session.execute(text("DELETE FROM group_scores"))
    session.commit()
    session.close()

def _create_group(session, run_id, entity_name, norm_metrics, transitions, warnings=None):
    calc = {
        "technical": {"unchanged": True},
        "macro": {"unchanged": True},
        "fundamental": {
            "raw_aggregation": {"unchanged": True},
            "metric_normalization": {"metrics": norm_metrics},
            "structural_transition_scores": transitions
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
        calculation_details=calc,
        warnings=warnings or []
    )
    session.add(g)

def test_fundamental_sector_pillar_score_service(disc_session):
    run_id = "run_pillar"
    
    def _m(score, app=True):
        return {"score": score, "applicable": app}
        
    def _t(num_sc, num_cnt, fall_sc, fall_cnt):
        return {
            "numeric_status_count": num_cnt,
            "fallback_score": fall_sc,
            "fallback_status_count": fall_cnt
        }

    # 1. Numeric-only, 7. Growth both, 17. Status boundaries (80+ = VERY_STRONG)
    _create_group(disc_session, run_id, "SEC_NUM", 
        norm_metrics={
            "sales_growth_pct": _m(80.0),
            "net_profit_growth_pct": _m(90.0)
        },
        transitions={
            "net_profit": _t(None, 10, None, 0)
        }
    )
    
    # 2. Fallback-only, 8. Growth one component, 16. Partial warning
    _create_group(disc_session, run_id, "SEC_FALL", 
        norm_metrics={
            "sales_growth_pct": _m(None),
            "net_profit_growth_pct": _m(None) # Missing numeric
        },
        transitions={
            "net_profit": _t(None, 0, 70.0, 10)
        }
    )
    
    # 3. Mixed numeric/fallback, 4. Missing num!=0, 5. Missing fall!=0, 6. Coverage, 11. Borrowing blend, 15. CC blend
    _create_group(disc_session, run_id, "SEC_MIX", 
        norm_metrics={
            "net_profit_growth_pct": _m(70.0),
            "latest_ocf_to_pat": _m(None), # CC missing num, fall exists
            "borrowing_change_pct": _m(80.0)
        },
        transitions={
            "net_profit": _t(None, 14, 90.0, 6),
            "cash_conversion": _t(None, 5, 50.0, 5), # 5 num (no score), 5 fall (scored)
            "borrowing": _t(None, 10, None, 5) # 10 num (scored), 5 fall (no score)
        }
    )
    
    # 9. Profitability 60/40, 10. Financial 60/40, 14. EQ weighted
    _create_group(disc_session, run_id, "SEC_FULL", 
        norm_metrics={
            "latest_operating_margin_pct": _m(60.0),
            "operating_margin_change_pp": _m(40.0),
            "debt_to_equity": _m(50.0),
            "borrowing_change_pct": _m(100.0),
            "latest_ocf_to_pat": _m(80.0),
            "ocf_to_pat_change": _m(60.0),
            "positive_pat_period_ratio": _m(40.0),
            "pat_growth_volatility_pct": _m(20.0)
        },
        transitions={
            "borrowing": _t(None, 10, None, 0),
            "cash_conversion": _t(None, 10, None, 0)
        }
    )
    
    # 12. Financial-only receives N_A
    _create_group(disc_session, run_id, "SEC_FIN", 
        norm_metrics={
            "debt_to_equity": _m(None, app=False)
        },
        transitions={}
    )
    
    disc_session.commit()
    
    svc = FundamentalSectorPillarScoreService(disc_session)
    svc.calculate_pillar_scores(run_id)
    
    def get_p(name):
        g = disc_session.query(GroupScore).filter_by(entity_name=name).first()
        return g.calculation_details["fundamental"]["pillar_scores"], g.warnings

    # 1, 7, 17
    p_num, w_num = get_p("SEC_NUM")
    g_num = p_num["growth"]
    assert g_num["components"]["net_profit_growth"]["effective_score"] == 90.0
    # Score: 80*0.5 + 90*0.5 = 85
    assert g_num["score"] == 85.0
    assert g_num["status"] == "VERY_STRONG"
    assert "GROWTH_PILLAR_UNAVAILABLE" not in w_num
    assert "GROWTH_PILLAR_PARTIAL" not in w_num
    
    # 2, 8, 16
    p_fall, w_fall = get_p("SEC_FALL")
    g_fall = p_fall["growth"]
    assert g_fall["components"]["net_profit_growth"]["effective_score"] == 70.0
    assert g_fall["components"]["sales_growth"]["available"] is False
    assert g_fall["score"] == 70.0
    assert g_fall["status"] == "STRONG"
    assert "GROWTH_PILLAR_PARTIAL" in w_fall
    assert "PROFITABILITY_PILLAR_UNAVAILABLE" in w_fall
    
    # 3, 4, 5, 6, 11, 15
    p_mix, _ = get_p("SEC_MIX")
    np = p_mix["growth"]["components"]["net_profit_growth"]
    # (70*14 + 90*6) / 20 = (980 + 540) / 20 = 1520 / 20 = 76.0
    assert np["effective_score"] == 76.0
    assert np["evidence_coverage_pct"] == 100.0
    
    cc = p_mix["earnings_quality"]["components"]["latest_cash_conversion"]
    # 5 num (no score) + 5 fall (50) -> avail_num=0, avail_fall=5
    # eff_score = (50*5) / 5 = 50.0
    assert cc["effective_score"] == 50.0
    assert cc["evidence_coverage_pct"] == 50.0 # 5 / 10
    
    bor = p_mix["financial_strength"]["components"]["borrowing_trend"]
    # 10 num (80) + 5 fall (no score) -> avail_num=10, avail_fall=0
    # eff_score = (80*10) / 10 = 80.0
    assert bor["effective_score"] == 80.0
    assert bor["evidence_coverage_pct"] == 66.67 # 10 / 15
    
    # 9, 10, 14
    p_full, _ = get_p("SEC_FULL")
    prof = p_full["profitability"]
    # 60*0.6 + 40*0.4 = 36 + 16 = 52.0
    assert prof["score"] == 52.0
    assert prof["status"] == "NEUTRAL"
    
    fin = p_full["financial_strength"]
    # 50*0.6 + 100*0.4 = 30 + 40 = 70.0
    assert fin["score"] == 70.0
    assert fin["status"] == "STRONG"
    
    eq = p_full["earnings_quality"]
    # 80*0.4 + 60*0.2 + 40*0.25 + 20*0.15 = 32 + 12 + 10 + 3 = 57.0
    assert eq["score"] == 57.0
    
    # 12
    p_fin, w_fin = get_p("SEC_FIN")
    assert p_fin["financial_strength"]["applicable"] is False
    assert p_fin["financial_strength"]["status"] == "N_A"
    assert "FINANCIAL_STRENGTH_PILLAR_UNAVAILABLE" not in w_fin
    
    # 18, 19
    g = disc_session.query(GroupScore).filter_by(entity_name="SEC_FULL").first()
    assert g.calculation_details["technical"]["unchanged"] is True
    assert g.calculation_details["macro"]["unchanged"] is True
    assert g.calculation_details["fundamental"]["raw_aggregation"]["unchanged"] is True
    assert "metric_normalization" in g.calculation_details["fundamental"]
    
    # 20
    svc.calculate_pillar_scores(run_id)
    p_full2, _ = get_p("SEC_FULL")
    assert p_full2["profitability"]["score"] == 52.0

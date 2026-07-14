"""
Tests for FundamentalIndustryPillarScoreService.
"""
import uuid
import pytest
from sqlalchemy import text

from database import DiscoverySessionLocal
from models.discovery import GroupScore
from services.fundamental.fundamental_industry_pillar_score import FundamentalIndustryPillarScoreService

@pytest.fixture
def disc_session():
    session = DiscoverySessionLocal()
    session.execute(text("DELETE FROM group_scores"))
    session.commit()
    yield session
    session.execute(text("DELETE FROM group_scores"))
    session.commit()
    session.close()

def _create_ind(session, run_id, entity_name, norm_metrics, transitions, std_debt_cnt=10, warnings=None):
    calc = {
        "technical": {"unchanged": True},
        "macro": {"unchanged": True},
        "fundamental": {
            "raw_aggregation": {
                "standard_debt_rule_applicable_count": std_debt_cnt,
                "unchanged": True
            },
            "metric_normalization": {"metrics": norm_metrics},
            "structural_transition_scores": transitions
        }
    }
        
    g = GroupScore(
        id=str(uuid.uuid4()),
        run_id=run_id,
        entity_type="INDUSTRY",
        entity_name=entity_name,
        parent_sector="SectorA",
        parent_industry="",
        horizon="1Y",
        calculation_details=calc,
        warnings=warnings or []
    )
    session.add(g)

def test_fundamental_industry_pillar_score_service(disc_session):
    run_id = "run_ind_pillar"
    
    def _m(score, app=True):
        return {"score": score, "applicable": app}
        
    def _t(num_sc, num_cnt, fall_sc, fall_cnt):
        return {
            "numeric_status_count": num_cnt,
            "fallback_score": fall_sc,
            "fallback_status_count": fall_cnt
        }

    # 1. Numeric-only, 7. Growth 50/50, 18. Status boundary
    _create_ind(disc_session, run_id, "IND_NUM", 
        norm_metrics={
            "sales_growth_pct": _m(80.0),
            "net_profit_growth_pct": _m(90.0)
        },
        transitions={
            "net_profit": _t(None, 10, None, 0)
        }
    )
    
    # 2. Fallback-only, 8. Growth 1 comp, 17. Partial warning
    _create_ind(disc_session, run_id, "IND_FALL", 
        norm_metrics={
            "sales_growth_pct": _m(None),
            "net_profit_growth_pct": _m(None)
        },
        transitions={
            "net_profit": _t(None, 0, 70.0, 10)
        }
    )
    
    # 3, 4, 5, 6, 12, 16. Mixed blending, missing missing missing
    _create_ind(disc_session, run_id, "IND_MIX", 
        norm_metrics={
            "net_profit_growth_pct": _m(70.0),
            "latest_ocf_to_pat": _m(None),
            "borrowing_change_pct": _m(80.0)
        },
        transitions={
            "net_profit": _t(None, 14, 90.0, 6),
            "cash_conversion": _t(None, 5, 50.0, 5),
            "borrowing": _t(None, 10, None, 5)
        }
    )
    
    # 9. Profitability 60/40, 10. Profitability 1 comp, 11. FinStr 60/40, 14. Mixed remains fin applicable, 15. EQ weighted
    _create_ind(disc_session, run_id, "IND_FULL", 
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
        },
        std_debt_cnt=10
    )
    
    _create_ind(disc_session, run_id, "IND_PROF1",
        norm_metrics={
            "latest_operating_margin_pct": _m(60.0),
            "operating_margin_change_pp": _m(None)
        },
        transitions={}
    )
    
    # 13. Financial only N_A
    _create_ind(disc_session, run_id, "IND_FIN", 
        norm_metrics={
            "debt_to_equity": _m(None, app=False)
        },
        transitions={},
        std_debt_cnt=0
    )
    
    disc_session.commit()
    
    svc = FundamentalIndustryPillarScoreService(disc_session)
    svc.calculate_pillar_scores(run_id)
    
    def get_p(name):
        g = disc_session.query(GroupScore).filter_by(entity_name=name).first()
        return g.calculation_details["fundamental"]["pillar_scores"], g.warnings

    # 1, 7, 18
    p_num, w_num = get_p("IND_NUM")
    g_num = p_num["growth"]
    assert g_num["components"]["net_profit_growth"]["effective_score"] == 90.0
    assert g_num["score"] == 85.0
    assert g_num["status"] == "VERY_STRONG"
    assert "GROWTH_PILLAR_UNAVAILABLE" not in w_num
    
    # 2, 8, 17
    p_fall, w_fall = get_p("IND_FALL")
    g_fall = p_fall["growth"]
    assert g_fall["components"]["net_profit_growth"]["effective_score"] == 70.0
    assert g_fall["score"] == 70.0
    assert "GROWTH_PILLAR_PARTIAL" in w_fall
    assert "PROFITABILITY_PILLAR_UNAVAILABLE" in w_fall
    
    # 3, 4, 5, 6, 12, 16
    p_mix, _ = get_p("IND_MIX")
    np = p_mix["growth"]["components"]["net_profit_growth"]
    assert np["effective_score"] == 76.0 # (70*14 + 90*6) / 20
    assert np["evidence_coverage_pct"] == 100.0
    
    cc = p_mix["earnings_quality"]["components"]["latest_cash_conversion"]
    assert cc["effective_score"] == 50.0 # 5 fall @ 50, 5 num @ None
    assert cc["evidence_coverage_pct"] == 50.0
    
    bor = p_mix["financial_strength"]["components"]["borrowing_trend"]
    assert bor["effective_score"] == 80.0 # 10 num @ 80, 5 fall @ None
    assert bor["evidence_coverage_pct"] == 66.67
    
    # 9, 11, 14, 15
    p_full, _ = get_p("IND_FULL")
    assert p_full["profitability"]["score"] == 52.0
    assert p_full["financial_strength"]["score"] == 70.0
    assert p_full["financial_strength"]["applicable"] is True
    assert p_full["earnings_quality"]["score"] == 57.0
    
    # 10
    p_prof1, _ = get_p("IND_PROF1")
    assert p_prof1["profitability"]["score"] == 60.0
    
    # 13
    p_fin, w_fin = get_p("IND_FIN")
    assert p_fin["financial_strength"]["applicable"] is False
    assert p_fin["financial_strength"]["status"] == "N_A"
    assert "FINANCIAL_STRENGTH_PILLAR_UNAVAILABLE" not in w_fin
    
    # 19, 20
    g = disc_session.query(GroupScore).filter_by(entity_name="IND_FULL").first()
    assert g.calculation_details["technical"]["unchanged"] is True
    assert g.calculation_details["macro"]["unchanged"] is True
    assert g.calculation_details["fundamental"]["raw_aggregation"]["unchanged"] is True
    
    # 21
    svc.calculate_pillar_scores(run_id)
    p_full2, _ = get_p("IND_FULL")
    assert p_full2["profitability"]["score"] == 52.0

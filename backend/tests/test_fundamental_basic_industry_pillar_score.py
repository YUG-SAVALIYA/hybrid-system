"""
Tests for FundamentalBasicIndustryPillarScoreService.
"""
import uuid
import pytest
from sqlalchemy import text

from database import DiscoverySessionLocal
from models.discovery import GroupScore
from services.fundamental.fundamental_basic_industry_pillar_score import FundamentalBasicIndustryPillarScoreService

@pytest.fixture
def disc_session():
    session = DiscoverySessionLocal()
    session.execute(text("DELETE FROM group_scores"))
    session.commit()
    yield session
    session.execute(text("DELETE FROM group_scores"))
    session.commit()
    session.close()

def _create_bi(session, run_id, entity_name, norm_metrics, transitions, std_debt_cnt=5, warnings=None):
    calc = {
        "technical": {"unchanged": True},
        "macro": {"unchanged": True},
        "fundamental": {
            "raw_aggregation": {
                "standard_debt_rule_applicable_count": std_debt_cnt,
                "unchanged": True
            },
            "metric_normalization": {"metrics": norm_metrics, "unchanged": True},
            "structural_transition_scores": transitions,
            "unchanged": True
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
        calculation_details=calc,
        warnings=warnings or []
    )
    session.add(g)

def test_fundamental_basic_industry_pillar_score_service(disc_session):
    run_id = "run_bi_pillar"
    
    def _m(score):
        return {"score": score}

    def _t(num_cnt, fall_sc, fall_cnt):
        return {
            "numeric_status_count": num_cnt,
            "fallback_score": fall_sc,
            "fallback_status_count": fall_cnt
        }

    # 1. Numeric only, 7. Growth 50/50, 9. Profitability 60/40, 11. FS 60/40, 15. EQ weighted, 18. Status bounds
    _create_bi(disc_session, run_id, "BI_ALL", 
        norm_metrics={
            "sales_growth_pct": _m(80.0),
            "net_profit_growth_pct": _m(70.0), # Num only
            "latest_operating_margin_pct": _m(90.0),
            "operating_margin_change_pp": _m(50.0),
            "debt_to_equity": _m(85.0),
            "borrowing_change_pct": _m(70.0), # Num only
            "latest_ocf_to_pat": _m(60.0), # Num only
            "ocf_to_pat_change": _m(40.0),
            "positive_pat_period_ratio": _m(30.0),
            "pat_growth_volatility_pct": _m(20.0)
        },
        transitions={
            "net_profit": _t(5, None, 0),
            "borrowing": _t(5, None, 0),
            "cash_conversion": _t(5, None, 0)
        }
    )
    
    # 2. Fallback only, 8. Growth 1 comp (missing num), 10. Profitability 1 comp, 17. Partial/Unavailable
    _create_bi(disc_session, run_id, "BI_FALLBACK",
        norm_metrics={
            "sales_growth_pct": _m(None), # Missing num
            "net_profit_growth_pct": _m(None), # Fallback only
            "latest_operating_margin_pct": _m(90.0),
            "operating_margin_change_pp": _m(None), # Missing
        },
        transitions={
            "net_profit": _t(0, 80.0, 5),
            "borrowing": {},
            "cash_conversion": {}
        }
    )
    
    # 3. Mixed count-weighted, 4. Missing num not zero, 5. Missing fall not zero, 6. Evidence coverage, 12. Borrowing blending, 16. CC blending
    _create_bi(disc_session, run_id, "BI_MIXED",
        norm_metrics={
            "net_profit_growth_pct": _m(50.0),
            "borrowing_change_pct": _m(None), # Missing num
            "latest_ocf_to_pat": _m(60.0)
        },
        transitions={
            "net_profit": _t(3, 100.0, 2), # Eff = (50*3 + 100*2)/5 = 350/5 = 70
            "borrowing": _t(3, 40.0, 2), # Eff = (0 + 40*2)/2 = 40. num is missing, not zero! count becomes 0
            "cash_conversion": _t(4, None, 2) # Eff = (60*4 + 0)/4 = 60. fall is missing, not zero! count becomes 0
        }
    )
    
    # 13. Financial N_A
    _create_bi(disc_session, run_id, "BI_FIN",
        norm_metrics={"debt_to_equity": _m(10.0)},
        transitions={},
        std_debt_cnt=0
    )
    
    # 14. Mixed applicable
    _create_bi(disc_session, run_id, "BI_MIXED_APP",
        norm_metrics={"debt_to_equity": _m(10.0)},
        transitions={},
        std_debt_cnt=2
    )
    
    disc_session.commit()
    
    svc = FundamentalBasicIndustryPillarScoreService(disc_session)
    svc.calculate_pillar_scores(run_id)
    
    def get_p(name):
        g = disc_session.query(GroupScore).filter_by(entity_name=name).first()
        return g.calculation_details["fundamental"]["pillar_scores"], g.warnings
        
    # 1, 7, 9, 11, 15, 18
    p_all, w_all = get_p("BI_ALL")
    assert p_all["growth"]["score"] == 75.0 # (80*50 + 70*50)/100
    assert p_all["growth"]["status"] == "STRONG"
    
    assert p_all["profitability"]["score"] == 74.0 # (90*60 + 50*40)/100
    
    assert p_all["financial_strength"]["score"] == 79.0 # (85*60 + 70*40)/100
    
    assert p_all["earnings_quality"]["score"] == 42.5 # (60*40 + 40*20 + 30*25 + 20*15)/100 = (2400 + 800 + 750 + 300)/100 = 4250/100 = 42.5
    
    assert len(w_all) == 0
    
    # 2, 8, 10, 17
    p_fall, w_fall = get_p("BI_FALLBACK")
    assert p_fall["growth"]["score"] == 80.0 # Only fallback
    assert p_fall["growth"]["coverage_pct"] == 50.0
    
    assert p_fall["profitability"]["score"] == 90.0
    assert p_fall["profitability"]["coverage_pct"] == 60.0
    
    assert "GROWTH_PILLAR_PARTIAL" in w_fall
    assert "PROFITABILITY_PILLAR_PARTIAL" in w_fall
    assert "FINANCIAL_STRENGTH_PILLAR_UNAVAILABLE" in w_fall
    
    # 3, 4, 5, 6, 12, 16
    p_mix, _ = get_p("BI_MIXED")
    assert p_mix["growth"]["components"]["net_profit_growth"]["effective_score"] == 70.0
    assert p_mix["growth"]["components"]["net_profit_growth"]["evidence_coverage_pct"] == 100.0
    
    assert p_mix["financial_strength"]["components"]["borrowing_trend"]["effective_score"] == 40.0
    assert p_mix["financial_strength"]["components"]["borrowing_trend"]["evidence_coverage_pct"] == 40.0 # 2 / 5
    
    assert p_mix["earnings_quality"]["components"]["latest_cash_conversion"]["effective_score"] == 60.0
    assert p_mix["earnings_quality"]["components"]["latest_cash_conversion"]["evidence_coverage_pct"] == 66.67 # 4 / 6
    
    # 13
    p_fin, w_fin = get_p("BI_FIN")
    assert p_fin["financial_strength"]["applicable"] is False
    assert p_fin["financial_strength"]["status"] == "N_A"
    assert "FINANCIAL_STRENGTH_PILLAR_UNAVAILABLE" not in w_fin
    
    # 14
    p_app, _ = get_p("BI_MIXED_APP")
    assert p_app["financial_strength"]["applicable"] is True
    
    # 19, 20
    g = disc_session.query(GroupScore).filter_by(entity_name="BI_ALL").first()
    assert g.calculation_details["technical"]["unchanged"] is True
    assert g.calculation_details["macro"]["unchanged"] is True
    assert g.calculation_details["fundamental"]["raw_aggregation"]["unchanged"] is True
    assert g.calculation_details["fundamental"]["metric_normalization"]["unchanged"] is True
    assert g.calculation_details["fundamental"]["unchanged"] is True
    
    # 21
    svc.calculate_pillar_scores(run_id)
    p_all2, _ = get_p("BI_ALL")
    assert p_all2["growth"]["score"] == 75.0

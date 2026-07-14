"""
Tests for FundamentalBasicIndustryScoreService.
"""
import uuid
import pytest
from sqlalchemy import text

from database import DiscoverySessionLocal
from models.discovery import GroupScore
from services.fundamental.fundamental_basic_industry_score import FundamentalBasicIndustryScoreService

@pytest.fixture
def disc_session():
    session = DiscoverySessionLocal()
    session.execute(text("DELETE FROM group_scores"))
    session.commit()
    yield session
    session.execute(text("DELETE FROM group_scores"))
    session.commit()
    session.close()

def _create_bi(session, run_id, entity_name, pillars, avail_cnt=5, warnings=None):
    calc = {
        "technical": {"unchanged": True},
        "macro": {"unchanged": True},
        "fundamental": {
            "raw_aggregation": {
                "fundamental_score_available_count": avail_cnt,
                "unchanged": True
            },
            "metric_normalization": {"unchanged": True},
            "structural_transition_scores": {"unchanged": True},
            "pillar_scores": pillars
        }
    }
        
    g = GroupScore(
        id=str(uuid.uuid4()),
        run_id=run_id,
        entity_type="BASIC_INDUSTRY",
        entity_name=entity_name,
        parent_sector="SectorA",
        parent_industry="IndA",
        horizon="1Y",
        calculation_details=calc,
        warnings=warnings or []
    )
    session.add(g)

def test_fundamental_basic_industry_score_service(disc_session):
    run_id = "run_bi_score"
    
    def _p(score, app=True):
        stat = "NEUTRAL"
        if score is None:
            stat = "UNAVAILABLE" if app else "N_A"
        elif isinstance(score, str) and score == "inf":
            stat = "UNAVAILABLE"
        elif score >= 80: stat = "VERY_STRONG"
        elif score >= 65: stat = "STRONG"
        elif score >= 35: stat = "WEAK"
        elif score < 35: stat = "VERY_WEAK"
        return {"score": score, "applicable": app, "status": stat}

    # 1, 2, 6, 11, 15
    _create_bi(disc_session, run_id, "BI_ALL", {
        "growth": _p(80.0),
        "profitability": _p(70.0),
        "financial_strength": _p(60.0),
        "earnings_quality": _p(50.0)
    })
    
    # 3. One applicable unavailable -> 75% coverage
    _create_bi(disc_session, run_id, "BI_ONE_MISS", {
        "growth": _p(80.0),
        "profitability": _p(70.0),
        "financial_strength": _p(60.0),
        "earnings_quality": _p(None)
    })
    
    # 4. Two applicable unavailable -> 50% coverage, 12. Below 75% ineligible, 13. Preserves score
    _create_bi(disc_session, run_id, "BI_TWO_MISS", {
        "growth": _p(80.0),
        "profitability": _p(70.0),
        "financial_strength": _p(None),
        "earnings_quality": _p(None)
    })
    
    # 5. No pillar available
    _create_bi(disc_session, run_id, "BI_NONE", {
        "growth": _p(None),
        "profitability": _p(None),
        "financial_strength": _p(None),
        "earnings_quality": _p(None)
    })
    
    # 7, 8. Financial industry, 3 avail -> 100% coverage, 10. No partial warning
    _create_bi(disc_session, run_id, "BI_FIN100", {
        "growth": _p(80.0),
        "profitability": _p(70.0),
        "financial_strength": _p(None, app=False),
        "earnings_quality": _p(60.0)
    })
    
    # 9. Financial industry, 2 avail -> 66.67%
    _create_bi(disc_session, run_id, "BI_FIN66", {
        "growth": _p(80.0),
        "profitability": _p(70.0),
        "financial_strength": _p(None, app=False),
        "earnings_quality": _p(None)
    })
    
    # 14. <2 constituents (minimum is 2 for basic industries)
    _create_bi(disc_session, run_id, "BI_SMALL", {
        "growth": _p(80.0),
        "profitability": _p(70.0),
        "financial_strength": _p(60.0),
        "earnings_quality": _p(50.0)
    }, avail_cnt=1)
    
    # 16. Non-finite
    _create_bi(disc_session, run_id, "BI_INF", {
        "growth": _p("inf"),
        "profitability": _p(70.0),
        "financial_strength": _p(60.0),
        "earnings_quality": _p(50.0)
    })
    
    disc_session.commit()
    
    svc = FundamentalBasicIndustryScoreService(disc_session)
    svc.calculate_basic_industry_scores(run_id)
    
    def get_f(name):
        g = disc_session.query(GroupScore).filter_by(entity_name=name).first()
        return g.calculation_details["fundamental"]["final_score"], g.warnings, g.fundamental_score

    # 1, 2, 6, 11, 15
    f_all, w_all, s_all = get_f("BI_ALL")
    assert f_all["coverage_pct"] == 100.0
    assert f_all["eligible_for_selection"] is True
    assert f_all["score"] == 65.0 # (80+70+60+50)/4
    assert s_all == 65.0
    assert f_all["status"] == "STRONG"
    assert len(w_all) == 0
    
    # 3
    f_one, w_one, _ = get_f("BI_ONE_MISS")
    assert f_one["coverage_pct"] == 75.0
    assert f_one["eligible_for_selection"] is True
    assert f_one["score"] == 70.0 # (80+70+60)/3
    assert "BASIC_INDUSTRY_FUNDAMENTAL_SCORE_PARTIAL" in w_one
    assert "BASIC_INDUSTRY_FUNDAMENTAL_LOW_COVERAGE" not in w_one
    
    # 4, 12, 13
    f_two, w_two, _ = get_f("BI_TWO_MISS")
    assert f_two["coverage_pct"] == 50.0
    assert f_two["eligible_for_selection"] is False
    assert f_two["score"] == 75.0 # (80+70)/2
    assert "BASIC_INDUSTRY_FUNDAMENTAL_LOW_COVERAGE" in w_two
    
    # 5
    f_none, w_none, _ = get_f("BI_NONE")
    assert f_none["coverage_pct"] == 0.0
    assert f_none["eligible_for_selection"] is False
    assert f_none["score"] is None
    assert f_none["status"] == "UNAVAILABLE"
    assert "BASIC_INDUSTRY_FUNDAMENTAL_SCORE_UNAVAILABLE" in w_none
    
    # 7, 8, 10
    f_f1, w_f1, _ = get_f("BI_FIN100")
    assert f_f1["coverage_pct"] == 100.0
    assert f_f1["applicable_weight"] == 75.0
    assert f_f1["available_weight"] == 75.0
    assert f_f1["eligible_for_selection"] is True
    assert f_f1["score"] == 70.0 # (80+70+60)/3
    assert len(w_f1) == 0
    
    # 9
    f_f2, w_f2, _ = get_f("BI_FIN66")
    assert f_f2["coverage_pct"] == 66.67
    assert f_f2["eligible_for_selection"] is False
    assert f_f2["score"] == 75.0
    assert "BASIC_INDUSTRY_FUNDAMENTAL_LOW_COVERAGE" in w_f2
    
    # 14
    f_small, w_small, _ = get_f("BI_SMALL")
    assert f_small["eligible_for_selection"] is False
    assert "INSUFFICIENT_FUNDAMENTAL_CONSTITUENTS" in w_small
    
    # 16
    f_inf, _, _ = get_f("BI_INF")
    assert f_inf["coverage_pct"] == 75.0
    assert f_inf["score"] == 60.0 # (70+60+50)/3
    
    # 17, 18
    g = disc_session.query(GroupScore).filter_by(entity_name="BI_ALL").first()
    assert g.calculation_details["technical"]["unchanged"] is True
    assert g.calculation_details["macro"]["unchanged"] is True
    assert g.calculation_details["fundamental"]["raw_aggregation"]["unchanged"] is True
    assert g.calculation_details["fundamental"]["metric_normalization"]["unchanged"] is True
    assert g.calculation_details["fundamental"]["structural_transition_scores"]["unchanged"] is True
    assert "growth" in g.calculation_details["fundamental"]["pillar_scores"]
    
    # 19
    svc.calculate_basic_industry_scores(run_id)
    f_all2, _, _ = get_f("BI_ALL")
    assert f_all2["score"] == 65.0

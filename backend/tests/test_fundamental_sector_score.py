"""
Tests for FundamentalSectorScoreService.
"""
import uuid
import pytest
from sqlalchemy import text

from database import DiscoverySessionLocal
from models.discovery import GroupScore
from services.fundamental.fundamental_group_score import FundamentalGroupScoreService

@pytest.fixture
def disc_session():
    session = DiscoverySessionLocal()
    session.execute(text("DELETE FROM group_scores"))
    session.commit()
    yield session
    session.execute(text("DELETE FROM group_scores"))
    session.commit()
    session.close()

def _create_group(session, run_id, entity_name, pillars, cnt=10):
    calc = {
        "technical": {"unchanged": True},
        "macro": {"unchanged": True},
        "fundamental": {
            "raw_aggregation": {
                "fundamental_score_available_count": cnt,
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
        entity_type="SECTOR",
        entity_name=entity_name,
        parent_sector="",
        parent_industry="",
        horizon="1Y",
        calculation_details=calc,
        warnings=["EXISTING_WARN"]
    )
    session.add(g)

def test_fundamental_sector_score_service(disc_session):
    run_id = "run_final"
    
    def _p(score, app=True, stat="NEUTRAL"):
        if not app:
            return {"score": None, "applicable": False, "status": "N_A", "reason": "N_A_NO_STANDARD_DEBT_RULE_COMPANIES"}
        if score is None:
            return {"score": None, "applicable": True, "status": "UNAVAILABLE"}
        return {"score": score, "applicable": True, "status": stat}

    # 1. All four pillars, 2. Equal 25% weight, 6. Std-sector cov, 11. Eligible (100%), 15. Boundaries
    _create_group(disc_session, run_id, "SEC_ALL", {
        "growth": _p(80.0, stat="VERY_STRONG"),
        "profitability": _p(70.0, stat="STRONG"),
        "financial_strength": _p(60.0, stat="NEUTRAL"),
        "earnings_quality": _p(40.0, stat="WEAK")
    }) # Score: (80+70+60+40)/4 = 250/4 = 62.5 -> NEUTRAL
    
    # 3. One applicable pillar unavailable
    _create_group(disc_session, run_id, "SEC_MISS_1", {
        "growth": _p(80.0),
        "profitability": _p(70.0),
        "financial_strength": _p(60.0),
        "earnings_quality": _p(None)
    }) # Cov: 75% -> eligible
    
    # 4. Two missing, 12. Coverage below 75 ineligible, 13. Score preserved
    _create_group(disc_session, run_id, "SEC_MISS_2", {
        "growth": _p(80.0),
        "profitability": _p(70.0),
        "financial_strength": _p(None),
        "earnings_quality": _p(None)
    }) # Cov: 50% -> ineligible
    
    # 5. No pillar available
    _create_group(disc_session, run_id, "SEC_MISS_ALL", {
        "growth": _p(None),
        "profitability": _p(None),
        "financial_strength": _p(None),
        "earnings_quality": _p(None)
    })
    
    # 7. Financial sector excludes FS, 8. 100% coverage, 10. N_A doesn't trigger partial
    _create_group(disc_session, run_id, "SEC_FIN_100", {
        "growth": _p(80.0),
        "profitability": _p(70.0),
        "financial_strength": _p(None, app=False),
        "earnings_quality": _p(60.0)
    }) # Score: (80+70+60)/3 = 70.0
    
    # 9. Financial missing 1 -> 66.67%
    _create_group(disc_session, run_id, "SEC_FIN_66", {
        "growth": _p(80.0),
        "profitability": _p(70.0),
        "financial_strength": _p(None, app=False),
        "earnings_quality": _p(None)
    })
    
    # 14. Insufficient constituent count
    _create_group(disc_session, run_id, "SEC_LOW_CNT", {
        "growth": _p(80.0),
        "profitability": _p(80.0),
        "financial_strength": _p(80.0),
        "earnings_quality": _p(80.0)
    }, cnt=4)
    
    # 16. Non-finite
    _create_group(disc_session, run_id, "SEC_INF", {
        "growth": _p("inf"),
        "profitability": _p(70.0),
        "financial_strength": _p(60.0),
        "earnings_quality": _p(40.0)
    })
    
    disc_session.commit()
    
    svc = FundamentalGroupScoreService(disc_session)
    svc.calculate_final_scores(run_id, entity_type="SECTOR")
    
    def get_f(name):
        g = disc_session.query(GroupScore).filter_by(entity_name=name).first()
        return g.calculation_details["fundamental"]["final_score"], g.warnings, g.fundamental_score

    # 1, 2, 6, 11, 15
    f_all, w_all, s_all = get_f("SEC_ALL")
    assert f_all["score"] == 62.5
    assert s_all == 62.5
    assert f_all["status"] == "NEUTRAL"
    assert f_all["coverage_pct"] == 100.0
    assert f_all["eligible_for_selection"] is True
    assert "SECTOR_FUNDAMENTAL_SCORE_PARTIAL" not in w_all
    
    # 3
    f_miss1, w_miss1, _ = get_f("SEC_MISS_1")
    assert f_miss1["coverage_pct"] == 75.0
    assert f_miss1["eligible_for_selection"] is True
    assert "SECTOR_FUNDAMENTAL_SCORE_PARTIAL" in w_miss1
    
    # 4, 12, 13
    f_miss2, w_miss2, s_miss2 = get_f("SEC_MISS_2")
    assert f_miss2["coverage_pct"] == 50.0
    assert f_miss2["eligible_for_selection"] is False
    assert f_miss2["score"] == 75.0
    assert "SECTOR_FUNDAMENTAL_LOW_COVERAGE" in w_miss2
    
    # 5
    f_miss_all, w_miss_all, _ = get_f("SEC_MISS_ALL")
    assert f_miss_all["score"] is None
    assert f_miss_all["status"] == "UNAVAILABLE"
    assert f_miss_all["eligible_for_selection"] is False
    assert "SECTOR_FUNDAMENTAL_SCORE_UNAVAILABLE" in w_miss_all
    
    # 7, 8, 10
    f_fin_100, w_fin_100, _ = get_f("SEC_FIN_100")
    assert f_fin_100["applicable_weight"] == 75.0
    assert f_fin_100["available_weight"] == 75.0
    assert f_fin_100["coverage_pct"] == 100.0
    assert f_fin_100["score"] == 70.0
    assert "SECTOR_FUNDAMENTAL_SCORE_PARTIAL" not in w_fin_100
    
    # 9
    f_fin_66, _, _ = get_f("SEC_FIN_66")
    assert f_fin_66["coverage_pct"] == 66.67
    assert f_fin_66["eligible_for_selection"] is False
    
    # 14
    f_low_cnt, w_low_cnt, _ = get_f("SEC_LOW_CNT")
    assert f_low_cnt["eligible_for_selection"] is False
    assert "INSUFFICIENT_FUNDAMENTAL_CONSTITUENTS" in w_low_cnt
    
    # 16
    f_inf, _, _ = get_f("SEC_INF")
    assert f_inf["coverage_pct"] == 75.0
    
    # 17, 18
    g = disc_session.query(GroupScore).filter_by(entity_name="SEC_ALL").first()
    assert g.calculation_details["technical"]["unchanged"] is True
    assert g.calculation_details["macro"]["unchanged"] is True
    fund = g.calculation_details["fundamental"]
    assert fund["raw_aggregation"]["unchanged"] is True
    assert fund["metric_normalization"]["unchanged"] is True
    assert fund["structural_transition_scores"]["unchanged"] is True
    assert "pillar_scores" in fund
    assert "EXISTING_WARN" in g.warnings
    
    # 19
    svc.calculate_final_scores(run_id, entity_type="SECTOR")
    f_all2, _, _ = get_f("SEC_ALL")
    assert f_all2["score"] == 62.5

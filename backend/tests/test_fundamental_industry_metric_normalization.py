"""
Tests for FundamentalIndustryMetricNormalizationService.
"""
import uuid
import pytest
from sqlalchemy import text

from database import DiscoverySessionLocal
from models.discovery import GroupScore
from services.fundamental.fundamental_industry_metric_normalization import FundamentalIndustryMetricNormalizationService

@pytest.fixture
def disc_session():
    session = DiscoverySessionLocal()
    session.execute(text("DELETE FROM group_scores"))
    session.commit()
    yield session
    session.execute(text("DELETE FROM group_scores"))
    session.commit()
    session.close()

def _create_ind(session, run_id, entity_name, parent_sector, metrics, avail_cnt=5):
    calc = {
        "technical": {"unchanged": True},
        "macro": {"unchanged": True},
        "fundamental": {
            "raw_aggregation": {
                "fundamental_score_available_count": avail_cnt,
                "metrics": metrics,
                "unchanged": True
            }
        }
    }
        
    g = GroupScore(
        id=str(uuid.uuid4()),
        run_id=run_id,
        entity_type="INDUSTRY",
        entity_name=entity_name,
        parent_sector=parent_sector,
        parent_industry="",
        horizon="1Y",
        calculation_details=calc
    )
    session.add(g)

def test_fundamental_industry_metric_normalization_service(disc_session):
    run_id = "run_norm"
    
    def _m(med, v=5, a=5, c=100.0, reason=None):
        return {"median": med, "valid_count": v, "applicable_count": a, "coverage_pct": c, "reason": reason}

    # 1. Compare only within parent, 3, 4, 5. Percentiles
    # SecA has Ind1 (lowest sales, highest debt), Ind2 (middle), Ind3 (highest sales, lowest debt)
    _create_ind(disc_session, run_id, "Ind1", "SecA", {
        "sales_growth_pct": _m(10.0), # Lowest HIB -> 0
        "debt_to_equity": _m(3.0)     # Highest LIB -> 0
    })
    _create_ind(disc_session, run_id, "Ind2", "SecA", {
        "sales_growth_pct": _m(20.0), # Middle HIB -> 50
        "debt_to_equity": _m(2.0)     # Middle LIB -> 50
    })
    _create_ind(disc_session, run_id, "Ind3", "SecA", {
        "sales_growth_pct": _m(30.0), # Highest HIB -> 100
        "debt_to_equity": _m(1.0)     # Lowest LIB -> 100
    })
    
    # 2. Same industry name different sector
    _create_ind(disc_session, run_id, "Ind1", "SecB", {
        "sales_growth_pct": _m(10.0), # Only one in SecB -> 50
        "debt_to_equity": _m(3.0)
    })
    
    # 6. Ties (average rank)
    _create_ind(disc_session, run_id, "IndT1", "SecTies", {"sales_growth_pct": _m(10.0)})
    _create_ind(disc_session, run_id, "IndT2", "SecTies", {"sales_growth_pct": _m(10.0)})
    _create_ind(disc_session, run_id, "IndT3", "SecTies", {"sales_growth_pct": _m(30.0)})
    # ranks: IndT1/T2 tie for 1,2 -> avg rank 1.5. Score = (1.5-1)/(3-1)*100 = 25
    # IndT3 rank 3 -> (3-1)/2*100 = 100
    
    # 7. Single-industry comparison
    _create_ind(disc_session, run_id, "IndSingle", "SecSingle", {"sales_growth_pct": _m(20.0)})
    
    # 8, 9, 10, 11, 14. Missing median, low count, low coverage, low constituent, non-finite
    _create_ind(disc_session, run_id, "IndBad", "SecBad", {
        "sales_growth_pct": _m(None), # RAW_MEDIAN_UNAVAILABLE
        "net_profit_growth_pct": _m(10.0, v=2), # INSUFFICIENT_METRIC_OBSERVATIONS
        "latest_operating_margin_pct": _m(10.0, c=50.0), # LOW_METRIC_COVERAGE
        "operating_margin_change_pp": _m("inf") # RAW_MEDIAN_UNAVAILABLE
    }, avail_cnt=2) # INSUFFICIENT_CONSTITUENTS (applies to all)
    
    # 12, 13. Financial-only N_A debt excluded from coverage
    _create_ind(disc_session, run_id, "IndFin", "SecFin", {
        "sales_growth_pct": _m(10.0),
        "debt_to_equity": _m(None, v=0, a=0, c=None, reason="N_A_NO_STANDARD_DEBT_RULE_COMPANIES"),
        "borrowing_change_pct": _m(None, v=0, a=0, c=None, reason="N_A_NO_STANDARD_DEBT_RULE_COMPANIES")
    })
    
    disc_session.commit()
    
    svc = FundamentalIndustryMetricNormalizationService(disc_session)
    svc.normalize_industry_metrics(run_id)
    
    def get_i(name, sec):
        g = disc_session.query(GroupScore).filter_by(entity_name=name, parent_sector=sec).first()
        return g.calculation_details["fundamental"]["metric_normalization"]

    # 1, 3, 4, 5
    i1 = get_i("Ind1", "SecA")
    i2 = get_i("Ind2", "SecA")
    i3 = get_i("Ind3", "SecA")
    
    sg1 = i1["metrics"]["sales_growth_pct"]
    assert sg1["score"] == 0.0
    assert sg1["comparison_set_size"] == 3
    
    sg2 = i2["metrics"]["sales_growth_pct"]
    assert sg2["score"] == 50.0
    
    sg3 = i3["metrics"]["sales_growth_pct"]
    assert sg3["score"] == 100.0
    
    dte1 = i1["metrics"]["debt_to_equity"]
    assert dte1["score"] == 0.0 # LIB, 3.0 is worst
    
    dte3 = i3["metrics"]["debt_to_equity"]
    assert dte3["score"] == 100.0 # LIB, 1.0 is best
    
    # 2. Same industry diff sector
    i1_b = get_i("Ind1", "SecB")
    assert i1_b["metrics"]["sales_growth_pct"]["score"] == 50.0
    
    # 6. Ties
    it1 = get_i("IndT1", "SecTies")
    it2 = get_i("IndT2", "SecTies")
    it3 = get_i("IndT3", "SecTies")
    assert it1["metrics"]["sales_growth_pct"]["score"] == 25.0
    assert it2["metrics"]["sales_growth_pct"]["score"] == 25.0
    assert it3["metrics"]["sales_growth_pct"]["score"] == 100.0
    
    # 7. Single industry
    isingle = get_i("IndSingle", "SecSingle")
    assert isingle["metrics"]["sales_growth_pct"]["score"] == 50.0
    assert isingle["metrics"]["sales_growth_pct"]["reason"] == "SINGLE_INDUSTRY_METRIC_COMPARISON"
    
    # 8, 9, 10, 11, 14
    ibad = get_i("IndBad", "SecBad")
    assert ibad["metrics"]["sales_growth_pct"]["eligible"] is False
    assert ibad["metrics"]["sales_growth_pct"]["reason"] == "INSUFFICIENT_CONSTITUENTS"
    
    # 12, 13
    ifin = get_i("IndFin", "SecFin")
    assert ifin["metrics"]["debt_to_equity"]["applicable"] is False
    assert ifin["metrics"]["debt_to_equity"]["reason"] == "N_A_NO_STANDARD_DEBT_RULE_COMPANIES"
    # Total metrics = 10. Applicable = 10 - 2 (debt/borrowing) = 8
    # Scored = 1 (sales growth)
    # cov = 1/8 * 100 = 12.5%
    assert ifin["applicable_metric_count"] == 8
    assert ifin["scored_metric_count"] == 1
    assert ifin["coverage_pct"] == 12.5
    
    # 15, 16
    g = disc_session.query(GroupScore).filter_by(entity_name="Ind1", parent_sector="SecA").first()
    assert g.calculation_details["technical"]["unchanged"] is True
    assert g.calculation_details["macro"]["unchanged"] is True
    assert g.calculation_details["fundamental"]["raw_aggregation"]["unchanged"] is True
    
    # 17
    svc.normalize_industry_metrics(run_id)
    i1_2 = get_i("Ind1", "SecA")
    assert i1_2["metrics"]["sales_growth_pct"]["score"] == 0.0

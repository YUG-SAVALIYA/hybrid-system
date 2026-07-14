"""
Tests for FundamentalBasicIndustryMetricNormalizationService.
"""
import uuid
import pytest
from sqlalchemy import text

from database import DiscoverySessionLocal
from models.discovery import GroupScore
from services.fundamental.fundamental_basic_industry_metric_normalization import FundamentalBasicIndustryMetricNormalizationService

@pytest.fixture
def disc_session():
    session = DiscoverySessionLocal()
    session.execute(text("DELETE FROM group_scores"))
    session.commit()
    yield session
    session.execute(text("DELETE FROM group_scores"))
    session.commit()
    session.close()

def _create_bi(session, run_id, entity_name, parent_sector, parent_industry, metrics, avail_cnt=5):
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
        entity_type="BASIC_INDUSTRY",
        entity_name=entity_name,
        parent_sector=parent_sector,
        parent_industry=parent_industry,
        horizon="1Y",
        calculation_details=calc
    )
    session.add(g)

def test_fundamental_basic_industry_metric_normalization_service(disc_session):
    run_id = "run_bi_norm"
    
    def _m(med, v=5, a=5, c=100.0, reason=None):
        return {"median": med, "valid_count": v, "applicable_count": a, "coverage_pct": c, "reason": reason}

    # 1. Compare within sec+ind, 3, 4, 5. Percentiles
    _create_bi(disc_session, run_id, "BI1", "SecA", "IndA", {
        "sales_growth_pct": _m(10.0), # Lowest HIB -> 0
        "debt_to_equity": _m(3.0)     # Highest LIB -> 0
    })
    _create_bi(disc_session, run_id, "BI2", "SecA", "IndA", {
        "sales_growth_pct": _m(20.0), # Middle HIB -> 50
        "debt_to_equity": _m(2.0)     # Middle LIB -> 50
    })
    _create_bi(disc_session, run_id, "BI3", "SecA", "IndA", {
        "sales_growth_pct": _m(30.0), # Highest HIB -> 100
        "debt_to_equity": _m(1.0)     # Lowest LIB -> 100
    })
    
    # 2. Same bi name different ind
    _create_bi(disc_session, run_id, "BI1", "SecA", "IndB", {
        "sales_growth_pct": _m(10.0), # Only one in SecA/IndB -> 50
        "debt_to_equity": _m(3.0)
    })
    
    # 6. Ties (average rank)
    _create_bi(disc_session, run_id, "BIT1", "SecT", "IndT", {"sales_growth_pct": _m(10.0)})
    _create_bi(disc_session, run_id, "BIT2", "SecT", "IndT", {"sales_growth_pct": _m(10.0)})
    _create_bi(disc_session, run_id, "BIT3", "SecT", "IndT", {"sales_growth_pct": _m(30.0)})
    
    # 7. Single-group comparison
    _create_bi(disc_session, run_id, "BISingle", "SecS", "IndS", {"sales_growth_pct": _m(20.0)})
    
    # 8, 9, 10, 11, 14. Missing median, low count, low coverage, low constituent, non-finite
    _create_bi(disc_session, run_id, "BIBad", "SecBad", "IndBad", {
        "sales_growth_pct": _m(None), # RAW_MEDIAN_UNAVAILABLE
        "net_profit_growth_pct": _m(10.0, v=2), # INSUFFICIENT_METRIC_OBSERVATIONS
        "latest_operating_margin_pct": _m(10.0, c=50.0), # LOW_METRIC_COVERAGE
        "operating_margin_change_pp": _m("inf") # RAW_MEDIAN_UNAVAILABLE
    }, avail_cnt=1) # INSUFFICIENT_CONSTITUENTS (applies to all, minimum is 2 for basic industries)
    
    # 12, 13. Financial-only N_A debt excluded from coverage
    _create_bi(disc_session, run_id, "BIFin", "SecFin", "IndFin", {
        "sales_growth_pct": _m(10.0),
        "debt_to_equity": _m(None, v=0, a=0, c=None, reason="N_A_NO_STANDARD_DEBT_RULE_COMPANIES"),
        "borrowing_change_pct": _m(None, v=0, a=0, c=None, reason="N_A_NO_STANDARD_DEBT_RULE_COMPANIES")
    })
    
    disc_session.commit()
    
    svc = FundamentalBasicIndustryMetricNormalizationService(disc_session)
    svc.normalize_basic_industry_metrics(run_id)
    
    def get_i(name, sec, ind):
        g = disc_session.query(GroupScore).filter_by(entity_name=name, parent_sector=sec, parent_industry=ind).first()
        return g.calculation_details["fundamental"]["metric_normalization"]

    # 1, 3, 4, 5
    b1 = get_i("BI1", "SecA", "IndA")
    b2 = get_i("BI2", "SecA", "IndA")
    b3 = get_i("BI3", "SecA", "IndA")
    
    sg1 = b1["metrics"]["sales_growth_pct"]
    assert sg1["score"] == 0.0
    assert sg1["comparison_set_size"] == 3
    
    sg2 = b2["metrics"]["sales_growth_pct"]
    assert sg2["score"] == 50.0
    
    sg3 = b3["metrics"]["sales_growth_pct"]
    assert sg3["score"] == 100.0
    
    dte1 = b1["metrics"]["debt_to_equity"]
    assert dte1["score"] == 0.0 
    
    dte3 = b3["metrics"]["debt_to_equity"]
    assert dte3["score"] == 100.0 
    
    # 2. Same name diff parent
    b1_b = get_i("BI1", "SecA", "IndB")
    assert b1_b["metrics"]["sales_growth_pct"]["score"] == 50.0
    
    # 6. Ties
    bt1 = get_i("BIT1", "SecT", "IndT")
    bt2 = get_i("BIT2", "SecT", "IndT")
    bt3 = get_i("BIT3", "SecT", "IndT")
    assert bt1["metrics"]["sales_growth_pct"]["score"] == 25.0
    assert bt2["metrics"]["sales_growth_pct"]["score"] == 25.0
    assert bt3["metrics"]["sales_growth_pct"]["score"] == 100.0
    
    # 7. Single group
    bsingle = get_i("BISingle", "SecS", "IndS")
    assert bsingle["metrics"]["sales_growth_pct"]["score"] == 50.0
    assert bsingle["metrics"]["sales_growth_pct"]["reason"] == "SINGLE_BASIC_INDUSTRY_METRIC_COMPARISON"
    
    # 8, 9, 10, 11, 14
    bbad = get_i("BIBad", "SecBad", "IndBad")
    assert bbad["metrics"]["sales_growth_pct"]["eligible"] is False
    assert bbad["metrics"]["sales_growth_pct"]["reason"] == "INSUFFICIENT_CONSTITUENTS"
    
    # 12, 13
    bfin = get_i("BIFin", "SecFin", "IndFin")
    assert bfin["metrics"]["debt_to_equity"]["applicable"] is False
    assert bfin["metrics"]["debt_to_equity"]["reason"] == "N_A_NO_STANDARD_DEBT_RULE_COMPANIES"
    assert bfin["applicable_metric_count"] == 8
    assert bfin["scored_metric_count"] == 1
    assert bfin["coverage_pct"] == 12.5
    
    # 15, 16
    g = disc_session.query(GroupScore).filter_by(entity_name="BI1", parent_sector="SecA", parent_industry="IndA").first()
    assert g.calculation_details["technical"]["unchanged"] is True
    assert g.calculation_details["macro"]["unchanged"] is True
    assert g.calculation_details["fundamental"]["raw_aggregation"]["unchanged"] is True
    
    # 17
    svc.normalize_basic_industry_metrics(run_id)
    b1_2 = get_i("BI1", "SecA", "IndA")
    assert b1_2["metrics"]["sales_growth_pct"]["score"] == 0.0

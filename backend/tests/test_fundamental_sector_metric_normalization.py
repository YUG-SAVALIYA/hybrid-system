"""
Tests for FundamentalSectorMetricNormalizationService.
"""
import uuid
import pytest
from sqlalchemy import text

from database import DiscoverySessionLocal
from models.discovery import GroupScore
from services.fundamental.fundamental_sector_metric_normalization import FundamentalSectorMetricNormalizationService

@pytest.fixture
def disc_session():
    session = DiscoverySessionLocal()
    session.execute(text("DELETE FROM group_scores"))
    session.commit()
    yield session
    session.execute(text("DELETE FROM group_scores"))
    session.commit()
    session.close()

def _create_group(session, run_id, entity_name, constituent_count=10, metrics=None):
    calc = {
        "technical": {"unchanged": True},
        "macro": {"unchanged": True},
        "fundamental": {
            "raw_aggregation": {
                "constituent_count": constituent_count,
                "metrics": metrics or {}
            }
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
        calculation_details=calc
    )
    session.add(g)

def test_fundamental_sector_metric_normalization_service(disc_session):
    run_id = "run_norm"
    
    def m_dict(median, valid=5, app=5, cov=100.0, reason=None):
        return {
            "median": median,
            "valid_count": valid,
            "applicable_count": app,
            "coverage_pct": cov,
            "reason": reason
        }

    # HIGHER_IS_BETTER: sales_growth_pct
    # LOWER_IS_BETTER: debt_to_equity
    
    # 1. Higher-is-better lowest/highest, 2. Lower-is-better lowest/highest, 3. Middle percentile, 4. Ties
    # Sector A: 10.0 (Rank 1, Score 0) | DTE: 1.0 (Rank 1, Score 0)
    # Sector B: 20.0 (Rank 2.5, Score 50) | DTE: 0.8 (Rank 2.5, Score 50)
    # Sector C: 20.0 (Rank 2.5, Score 50) | DTE: 0.8 (Rank 2.5, Score 50)
    # Sector D: 30.0 (Rank 4, Score 100) | DTE: 0.2 (Rank 4, Score 100)
    
    _create_group(disc_session, run_id, "SEC_A", metrics={
        "sales_growth_pct": m_dict(10.0), "debt_to_equity": m_dict(1.0)
    })
    _create_group(disc_session, run_id, "SEC_B", metrics={
        "sales_growth_pct": m_dict(20.0), "debt_to_equity": m_dict(0.8)
    })
    _create_group(disc_session, run_id, "SEC_C", metrics={
        "sales_growth_pct": m_dict(20.0), "debt_to_equity": m_dict(0.8)
    })
    _create_group(disc_session, run_id, "SEC_D", metrics={
        "sales_growth_pct": m_dict(30.0), "debt_to_equity": m_dict(0.2)
    })
    
    # 5. Single-sector comparison
    _create_group(disc_session, run_id, "SEC_SINGLE", metrics={
        "net_profit_growth_pct": m_dict(5.0)
    })
    
    # 6. Missing median
    _create_group(disc_session, run_id, "SEC_NO_MEDIAN", metrics={
        "sales_growth_pct": m_dict(None)
    })
    
    # 7. Fewer than 3 obs
    _create_group(disc_session, run_id, "SEC_LOW_OBS", metrics={
        "sales_growth_pct": m_dict(15.0, valid=2)
    })
    
    # 8. Coverage below 60%
    _create_group(disc_session, run_id, "SEC_LOW_COV", metrics={
        "sales_growth_pct": m_dict(15.0, cov=50.0)
    })
    
    # 9. Sector < 5 constituents
    _create_group(disc_session, run_id, "SEC_LOW_CONST", constituent_count=3, metrics={
        "sales_growth_pct": m_dict(15.0)
    })
    
    # 10, 11. Fully financial -> NA
    _create_group(disc_session, run_id, "SEC_FINANCIAL", metrics={
        "debt_to_equity": m_dict(None, valid=0, app=0, cov=None, reason="N_A_NO_STANDARD_DEBT_RULE_COMPANIES")
    })
    
    # 13. Non-finite
    _create_group(disc_session, run_id, "SEC_INF", metrics={
        "sales_growth_pct": m_dict("inf")
    })
    
    disc_session.commit()
    
    svc = FundamentalSectorMetricNormalizationService(disc_session)
    svc.normalize_metrics(run_id)
    
    def get_norm(name):
        g = disc_session.query(GroupScore).filter_by(entity_name=name).first()
        return g.calculation_details["fundamental"]["metric_normalization"]

    # 1, 2, 3, 4
    n_a = get_norm("SEC_A")
    assert n_a["metrics"]["sales_growth_pct"]["score"] == 0.0
    assert n_a["metrics"]["debt_to_equity"]["score"] == 0.0
    
    n_b = get_norm("SEC_B")
    assert n_b["metrics"]["sales_growth_pct"]["score"] == 50.0
    assert n_b["metrics"]["debt_to_equity"]["score"] == 50.0
    assert n_b["metrics"]["sales_growth_pct"]["rank"] == 2.5
    
    n_d = get_norm("SEC_D")
    assert n_d["metrics"]["sales_growth_pct"]["score"] == 100.0
    assert n_d["metrics"]["debt_to_equity"]["score"] == 100.0
    
    # 5
    n_single = get_norm("SEC_SINGLE")
    sg_s = n_single["metrics"]["net_profit_growth_pct"]
    assert sg_s["score"] == 50.0
    assert sg_s["reason"] == "SINGLE_SECTOR_METRIC_COMPARISON"
    
    # 6
    n_no = get_norm("SEC_NO_MEDIAN")
    sg = n_no["metrics"]["sales_growth_pct"]
    assert sg["eligible"] is False
    assert sg["reason"] == "RAW_MEDIAN_UNAVAILABLE"
    
    # 7
    sg = get_norm("SEC_LOW_OBS")["metrics"]["sales_growth_pct"]
    assert sg["eligible"] is False
    assert sg["reason"] == "INSUFFICIENT_METRIC_OBSERVATIONS"
    
    # 8
    sg = get_norm("SEC_LOW_COV")["metrics"]["sales_growth_pct"]
    assert sg["eligible"] is False
    assert sg["reason"] == "LOW_METRIC_COVERAGE"
    
    # 9
    sg = get_norm("SEC_LOW_CONST")["metrics"]["sales_growth_pct"]
    assert sg["eligible"] is False
    assert sg["reason"] == "INSUFFICIENT_CONSTITUENTS"
    
    # 10, 11
    n_fin = get_norm("SEC_FINANCIAL")
    dte = n_fin["metrics"]["debt_to_equity"]
    assert dte["applicable"] is False
    assert dte["reason"] == "N_A_NO_STANDARD_DEBT_RULE_COMPANIES"
    assert n_fin["applicable_metric_count"] == 9 # 10 total - 1 NA
    
    # 13
    sg = get_norm("SEC_INF")["metrics"]["sales_growth_pct"]
    assert sg["eligible"] is False
    assert sg["reason"] == "RAW_MEDIAN_UNAVAILABLE"
    
    # 14, 15
    g = disc_session.query(GroupScore).filter_by(entity_name="SEC_A").first()
    assert g.calculation_details["technical"]["unchanged"] is True
    assert g.calculation_details["macro"]["unchanged"] is True
    assert "raw_aggregation" in g.calculation_details["fundamental"]
    
    # 16
    svc.normalize_metrics(run_id)
    n_d2 = get_norm("SEC_D")
    assert n_d2["metrics"]["sales_growth_pct"]["score"] == 100.0

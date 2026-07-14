"""
Tests for TechnicalBasicIndustryAggregationService.
"""
import uuid
import pytest
from sqlalchemy import text, inspect

from database import DiscoverySessionLocal, source_engine
from models.discovery import CompanyTechnicalMetric, GroupScore
from services.technical.technical_basic_industry_aggregation import TechnicalBasicIndustryAggregationService

@pytest.fixture
def disc_session():
    session = DiscoverySessionLocal()
    session.execute(text("DELETE FROM company_technical_metrics"))
    session.execute(text("DELETE FROM group_scores"))
    session.commit()
    yield session
    session.execute(text("DELETE FROM company_technical_metrics"))
    session.execute(text("DELETE FROM group_scores"))
    session.commit()
    session.close()


def _populate_company(session, run_id, symbol, sector, industry, basic_industry,
                      ret_avail=True, c_ret=0.0, rel_ret=0.0,
                      vol_avail=True, vol_chg=0.0,
                      cons_avail=True, cons_score=0.0):
    rec = CompanyTechnicalMetric(
        id=str(uuid.uuid4()),
        run_id=run_id,
        source_company_id=f"comp_{symbol}",
        symbol=symbol,
        sector=sector,
        industry=industry,
        basic_industry=basic_industry,
        horizon="SHORT",
        return_available=ret_avail,
        company_return=c_ret,
        relative_return=rel_ret,
        volume_available=vol_avail,
        volume_change=vol_chg,
        consistency_available=cons_avail,
        company_consistency_score=cons_score
    )
    session.add(rec)
    session.commit()
    return rec


# ------------------------------------------------------------------ #
#  Grouping, Calculations, Low Coverage, Insufficient, Preservations   #
# ------------------------------------------------------------------ #

def test_basic_industry_aggregation_pipeline(disc_session):
    run_id = "test_run"
    
    # 1. Grouping
    # Tech -> Software -> B2B
    for i in range(2):
        _populate_company(disc_session, run_id, f"B2B_{i}", "Tech", "Software", "B2B", c_ret=10.0, rel_ret=5.0)
        
    # Tech -> Software -> B2C (Insufficient Constituents test)
    _populate_company(disc_session, run_id, "B2C_0", "Tech", "Software", "B2C", c_ret=10.0, rel_ret=5.0)
    
    # Auto -> Tires -> Rubber (Low coverage test)
    _populate_company(disc_session, run_id, "RUB_0", "Auto", "Tires", "Rubber", vol_avail=False)
    _populate_company(disc_session, run_id, "RUB_1", "Auto", "Tires", "Rubber", vol_avail=True, vol_chg=10.0, c_ret=10.0)
    
    # Auto -> Software -> B2B (Separate group test)
    for i in range(2):
        _populate_company(disc_session, run_id, f"AUTO_B2B_{i}", "Auto", "Software", "B2B", c_ret=20.0, rel_ret=10.0)
        
    svc = TechnicalBasicIndustryAggregationService(disc_session)
    svc.aggregate_basic_industries(run_id, "SHORT")
    
    results = disc_session.query(GroupScore).filter_by(run_id=run_id, entity_type="BASIC_INDUSTRY").all()
    assert len(results) == 4
    
    # Calculations check
    b2b_tech = [r for r in results if r.parent_sector == "Tech" and r.entity_name == "B2B"][0]
    assert b2b_tech.constituent_count == 2
    assert b2b_tech.parent_industry == "Software"
    assert b2b_tech.calculation_details["technical"]["return"]["mean_company_return"] == 10.0
    
    # Separate group
    b2b_auto = [r for r in results if r.parent_sector == "Auto" and r.entity_name == "B2B"][0]
    assert b2b_auto.calculation_details["technical"]["return"]["mean_company_return"] == 20.0
    
    # Insufficient constituents
    b2c = [r for r in results if r.entity_name == "B2C"][0]
    assert "INSUFFICIENT_CONSTITUENTS" in b2c.warnings
    assert b2c.calculation_details["technical"]["return"]["mean_company_return"] == 10.0
    
    # Low volume coverage
    rub = [r for r in results if r.entity_name == "Rubber"][0]
    assert rub.technical_volume_score is None
    assert "LOW_VOLUME_DATA_COVERAGE" in rub.warnings
    assert rub.calculation_details["technical"]["volume"]["volume_coverage"] == 50.0

def test_preservation_and_idempotency(disc_session):
    run_id = "test_run"
    _populate_company(disc_session, run_id, "C1", "A", "B", "C", c_ret=10.0)
    _populate_company(disc_session, run_id, "C2", "A", "B", "C", c_ret=10.0)
    
    # Create arbitrary fundamental score to preserve
    existing = GroupScore(
        id=str(uuid.uuid4()), run_id=run_id, entity_type="BASIC_INDUSTRY", entity_name="C",
        parent_sector="A", parent_industry="B", horizon="SHORT",
        fundamental_score=99.9,
        rank=1,
        calculation_details={"fundamental": {"growth": 100}}
    )
    disc_session.add(existing)
    disc_session.commit()
    
    inspector = inspect(source_engine)
    tables_before = set(inspector.get_table_names())
    
    svc = TechnicalBasicIndustryAggregationService(disc_session)
    svc.aggregate_basic_industries(run_id, "SHORT")
    
    gs = disc_session.query(GroupScore).filter_by(entity_name="C").first()
    
    # Idempotency (run it twice)
    svc.aggregate_basic_industries(run_id, "SHORT")
    gs2 = disc_session.query(GroupScore).filter_by(entity_name="C").first()
    
    # Preservations
    assert gs2.fundamental_score == 99.9
    assert gs2.rank == 1
    assert "fundamental" in gs2.calculation_details
    assert "technical" in gs2.calculation_details
    
    tables_after = set(inspector.get_table_names())
    assert tables_before == tables_after

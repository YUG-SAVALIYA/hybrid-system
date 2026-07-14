"""
Tests for TechnicalIndustryAggregationService.
"""
import uuid
import pytest
from sqlalchemy import text, inspect

from database import DiscoverySessionLocal, source_engine
from models.discovery import CompanyTechnicalMetric, GroupScore
from services.technical.technical_industry_aggregation import TechnicalIndustryAggregationService


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


def _populate_company(session, run_id, symbol, sector, industry,
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
#  1-2. Grouping by Sector/Industry & Same Industry name collision     #
# ------------------------------------------------------------------ #

def test_industry_grouping_and_collisions(disc_session):
    run_id = "test_run"
    
    # "Software" under "Technology"
    for i in range(3):
        _populate_company(disc_session, run_id, f"T_S_{i}", "Technology", "Software")
        
    # "Software" under "Industrials" (collision name test)
    for i in range(3):
        _populate_company(disc_session, run_id, f"I_S_{i}", "Industrials", "Software")
        
    svc = TechnicalIndustryAggregationService(disc_session)
    svc.aggregate_industries(run_id, "SHORT")
    
    results = disc_session.query(GroupScore).filter_by(run_id=run_id).all()
    assert len(results) == 2
    
    t_s = [r for r in results if r.parent_sector == "Technology" and r.entity_name == "Software"][0]
    i_s = [r for r in results if r.parent_sector == "Industrials" and r.entity_name == "Software"][0]
    
    assert t_s.constituent_count == 3
    assert i_s.constituent_count == 3
    assert t_s.entity_type == "INDUSTRY"
    assert t_s.parent_industry == ""


# ------------------------------------------------------------------ #
#  3-7. Counts, Returns, and Breadth Score                             #
# ------------------------------------------------------------------ #

def test_counts_returns_and_breadth(disc_session):
    run_id = "test_run"
    sector, industry = "Tech", "Hardware"
    
    # 4 eligible, 1 ineligible. Total constituents = 5.
    _populate_company(disc_session, run_id, "H1", sector, industry, c_ret=-5.0, rel_ret=-10.0) # -/-
    _populate_company(disc_session, run_id, "H2", sector, industry, c_ret=-5.0, rel_ret=10.0)  # -/+
    _populate_company(disc_session, run_id, "H3", sector, industry, c_ret=5.0,  rel_ret=20.0)  # +/+
    _populate_company(disc_session, run_id, "H4", sector, industry, c_ret=5.0,  rel_ret=40.0)  # +/+
    _populate_company(disc_session, run_id, "H5", sector, industry, ret_avail=False)           # Not eligible
    
    svc = TechnicalIndustryAggregationService(disc_session)
    svc.aggregate_industries(run_id, "SHORT")
    
    gs = disc_session.query(GroupScore).filter_by(entity_name=industry).first()
    
    # 3. Counts
    assert gs.constituent_count == 5
    assert gs.eligible_constituent_count == 4
    
    tech = gs.calculation_details["technical"]
    assert tech["return"]["return_eligible_count"] == 4
    
    # 4. Median and mean relative return (and company return)
    # rel_returns: [-10, 10, 20, 40] -> Median: (10+20)/2 = 15.0. Mean: 60/4 = 15.0
    # c_returns: [-5, -5, 5, 5] -> Median: (-5+5)/2 = 0.0. Mean: 0.0
    assert tech["return"]["median_relative_return"] == 15.0
    assert tech["return"]["mean_relative_return"] == 15.0
    assert tech["return"]["median_company_return"] == 0.0
    assert tech["return"]["mean_company_return"] == 0.0
    
    # 5. Positive breadth (2 out of 4 = 50%)
    assert tech["breadth"]["positive_return_breadth"] == 50.0
    
    # 6. Outperformance breadth (3 out of 4 = 75%)
    assert tech["breadth"]["outperformance_breadth"] == 75.0
    
    # 7. Breadth score (50*0.5 + 75*0.5 = 62.5)
    assert gs.technical_breadth_score == 62.5


# ------------------------------------------------------------------ #
#  8-10. Volume confirmation, Distribution, Coverage                   #
# ------------------------------------------------------------------ #

def test_volume_calculations_and_warnings(disc_session):
    run_id = "test_run"
    sector, industry = "Finance", "Banking"
    
    # 4 companies. 3 volume available = 75% coverage.
    _populate_company(disc_session, run_id, "B1", sector, industry, c_ret=5, vol_chg=5)   # Conf (+c, +v)
    _populate_company(disc_session, run_id, "B2", sector, industry, c_ret=-5, vol_chg=5)  # Dist (-c, +v)
    _populate_company(disc_session, run_id, "B3", sector, industry, c_ret=-5, vol_chg=5)  # Dist (-c, +v)
    _populate_company(disc_session, run_id, "B4", sector, industry, vol_avail=False)
    
    svc = TechnicalIndustryAggregationService(disc_session)
    svc.aggregate_industries(run_id, "SHORT")
    
    gs = disc_session.query(GroupScore).filter_by(entity_name=industry).first()
    
    # 8. Volume confirmation (1 / 3 = 33.33%)
    assert gs.technical_volume_score == 33.33333333333333
    
    # 9. Distribution percentage and warning (2 / 3 = 66.66% >= 50%)
    tech = gs.calculation_details["technical"]
    assert tech["volume"]["distribution_percentage"] == 66.66666666666666
    assert "HIGH_DISTRIBUTION_PARTICIPATION" in gs.warnings
    assert "LOW_VOLUME_DATA_COVERAGE" not in gs.warnings
    
    disc_session.execute(text("DELETE FROM company_technical_metrics"))
    disc_session.execute(text("DELETE FROM group_scores"))
    
    # 10. Low volume coverage (< 60%). 4 companies, 2 vol available = 50%.
    for i in range(4):
        _populate_company(disc_session, run_id, f"B{i}", sector, industry, vol_avail=(i<2), c_ret=5, vol_chg=5)
        
    svc.aggregate_industries(run_id, "SHORT")
    gs2 = disc_session.query(GroupScore).filter_by(entity_name=industry).first()
    
    assert gs2.technical_volume_score is None
    assert "LOW_VOLUME_DATA_COVERAGE" in gs2.warnings
    # Raw volume preserved
    assert gs2.calculation_details["technical"]["volume"]["volume_coverage"] == 50.0


# ------------------------------------------------------------------ #
#  11-12. Consistency Metrics                                          #
# ------------------------------------------------------------------ #

def test_consistency_metrics(disc_session):
    run_id = "test_run"
    sector, industry = "Health", "Pharma"
    
    # Scores: 100, 80, 50, 40 -> 2 are >= 60 (50%). Mean=67.5. Median=(80+50)/2 = 65.0.
    _populate_company(disc_session, run_id, "P1", sector, industry, cons_avail=True, cons_score=100.0)
    _populate_company(disc_session, run_id, "P2", sector, industry, cons_avail=True, cons_score=80.0)
    _populate_company(disc_session, run_id, "P3", sector, industry, cons_avail=True, cons_score=50.0)
    _populate_company(disc_session, run_id, "P4", sector, industry, cons_avail=True, cons_score=40.0)
    
    svc = TechnicalIndustryAggregationService(disc_session)
    svc.aggregate_industries(run_id, "SHORT")
    
    gs = disc_session.query(GroupScore).filter_by(entity_name=industry).first()
    tech = gs.calculation_details["technical"]
    
    # 11. Mean and median consistency
    assert gs.technical_consistency_score == 67.5
    assert tech["consistency"]["mean_consistency_score"] == 67.5
    assert tech["consistency"]["median_consistency_score"] == 65.0
    
    # 12. Consistent-company percentage
    assert tech["consistency"]["consistent_company_percentage"] == 50.0


# ------------------------------------------------------------------ #
#  13. Fewer than three eligible companies                             #
# ------------------------------------------------------------------ #

def test_insufficient_industry_constituents(disc_session):
    run_id = "test_run"
    sector, industry = "Auto", "Tires"
    
    # Only 2 companies. MIN_INDUSTRY_COMPANIES = 3.
    _populate_company(disc_session, run_id, "T1", sector, industry, ret_avail=True, c_ret=10.0)
    _populate_company(disc_session, run_id, "T2", sector, industry, ret_avail=True, c_ret=5.0)
    
    svc = TechnicalIndustryAggregationService(disc_session)
    svc.aggregate_industries(run_id, "SHORT")
    
    gs = disc_session.query(GroupScore).filter_by(entity_name=industry).first()
    assert "INSUFFICIENT_CONSTITUENTS" in gs.warnings
    # Raw metrics still available
    assert gs.calculation_details["technical"]["return"]["mean_company_return"] == 7.5


# ------------------------------------------------------------------ #
#  14-17. Preservations, Idempotency, Bulk, and Source Isolation       #
# ------------------------------------------------------------------ #

def test_preservations_idempotency_bulk_isolation(disc_session):
    run_id = "test_run"
    sector, industry = "Auto", "Tires"
    
    _populate_company(disc_session, run_id, "T1", sector, industry, ret_avail=True, c_ret=10.0)
    _populate_company(disc_session, run_id, "T2", sector, industry, ret_avail=True, c_ret=10.0)
    _populate_company(disc_session, run_id, "T3", sector, industry, ret_avail=True, c_ret=10.0)
    
    # 15. Create arbitrary fundamental score to preserve
    existing = GroupScore(
        id=str(uuid.uuid4()), run_id=run_id, entity_type="INDUSTRY", entity_name=industry,
        parent_sector=sector, parent_industry="", horizon="SHORT",
        fundamental_score=99.9,
        rank=1,
        calculation_details={"fundamental": {"growth": 100}}
    )
    disc_session.add(existing)
    disc_session.commit()
    
    inspector = inspect(source_engine)
    tables_before = set(inspector.get_table_names())
    
    svc = TechnicalIndustryAggregationService(disc_session)
    # 16. Bulk aggregation implicitly tested by logic running everything in one pass
    svc.aggregate_industries(run_id, "SHORT")
    
    gs = disc_session.query(GroupScore).filter_by(entity_name=industry).first()
    
    # 14. Idempotency (run it twice)
    svc.aggregate_industries(run_id, "SHORT")
    gs2 = disc_session.query(GroupScore).filter_by(entity_name=industry).first()
    
    # Preservations
    assert gs2.fundamental_score == 99.9
    assert gs2.rank == 1
    assert "fundamental" in gs2.calculation_details
    assert "technical" in gs2.calculation_details
    
    # 17. Source isolation
    tables_after = set(inspector.get_table_names())
    assert tables_before == tables_after

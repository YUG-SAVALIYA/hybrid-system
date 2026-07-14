"""
Tests for TechnicalSectorAggregationService.
"""
import uuid
import pytest
from sqlalchemy import text, inspect

from database import DiscoverySessionLocal, source_engine
from models.discovery import CompanyTechnicalMetric, GroupScore
from services.technical.technical_sector_aggregation import TechnicalSectorAggregationService


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


def _populate_company(session, run_id, symbol, sector, 
                      ret_avail=True, c_ret=0.0, rel_ret=0.0,
                      vol_avail=True, vol_chg=0.0,
                      cons_avail=True, cons_score=0.0):
    rec = CompanyTechnicalMetric(
        id=str(uuid.uuid4()),
        run_id=run_id,
        source_company_id=f"comp_{symbol}",
        symbol=symbol,
        sector=sector,
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
#  1-2. Breadth and Sector Entity Setup                                #
# ------------------------------------------------------------------ #

def test_breadth_score_and_sector_entity(disc_session):
    run_id = "test_run"
    sector = "Technology"
    
    # Needs at least 5 to avoid INSUFFICIENT_CONSTITUENTS warning
    # We want: 
    # c_ret > 0: 3 out of 5 (60% positive breadth)
    # rel_ret > 0: 4 out of 5 (80% outperformance breadth)
    # Breadth score = (60*0.5) + (80*0.5) = 70.0
    # Rel returns: [-10, 10, 20, 30, 40]
    # Median = 20. Mean = 18
    _populate_company(disc_session, run_id, "T1", sector, c_ret=-5.0, rel_ret=-10.0) # -/-
    _populate_company(disc_session, run_id, "T2", sector, c_ret=-5.0, rel_ret=10.0)  # -/+
    _populate_company(disc_session, run_id, "T3", sector, c_ret=5.0,  rel_ret=20.0)  # +/+
    _populate_company(disc_session, run_id, "T4", sector, c_ret=5.0,  rel_ret=30.0)  # +/+
    _populate_company(disc_session, run_id, "T5", sector, c_ret=5.0,  rel_ret=40.0)  # +/+
    
    svc = TechnicalSectorAggregationService(disc_session)
    svc.aggregate_sectors(run_id, "SHORT")
    
    gs = disc_session.query(GroupScore).filter_by(entity_name=sector).first()
    
    assert gs is not None
    assert gs.entity_type == "SECTOR"
    assert gs.parent_sector == ""
    assert gs.parent_industry == ""
    assert gs.constituent_count == 5
    assert gs.eligible_constituent_count == 5
    
    assert gs.technical_breadth_score == 70.0
    assert gs.calculation_details["median_relative_return"] == 20.0
    assert gs.calculation_details["mean_relative_return"] == 18.0
    assert gs.calculation_details["positive_return_breadth"] == 60.0
    assert gs.calculation_details["outperformance_breadth"] == 80.0


# ------------------------------------------------------------------ #
#  3. Volume confirmation & Distribution (and coverage)                #
# ------------------------------------------------------------------ #

def test_volume_scores_and_coverage(disc_session):
    run_id = "test_run"
    sector = "Finance"
    
    # 5 companies. 
    # If 2 have volume available, coverage is 40% (< 60%). Score = None.
    _populate_company(disc_session, run_id, "F1", sector, vol_avail=True)
    _populate_company(disc_session, run_id, "F2", sector, vol_avail=True)
    _populate_company(disc_session, run_id, "F3", sector, vol_avail=False)
    _populate_company(disc_session, run_id, "F4", sector, vol_avail=False)
    _populate_company(disc_session, run_id, "F5", sector, vol_avail=False)
    
    svc = TechnicalSectorAggregationService(disc_session)
    svc.aggregate_sectors(run_id, "SHORT")
    
    gs = disc_session.query(GroupScore).filter_by(entity_name=sector).first()
    assert gs.technical_volume_score is None
    assert "INSUFFICIENT_SECTOR_VOLUME_COVERAGE" in gs.warnings
    
    disc_session.execute(text("DELETE FROM company_technical_metrics"))
    disc_session.execute(text("DELETE FROM group_scores"))
    
    # Now let's do 100% volume coverage, with specific counts.
    # vol_conf: c_ret > 0 and vol_change > 0
    # dist: c_ret < 0 and vol_change > 0
    _populate_company(disc_session, run_id, "F1", sector, c_ret=5, vol_chg=5)   # Conf
    _populate_company(disc_session, run_id, "F2", sector, c_ret=5, vol_chg=5)   # Conf
    _populate_company(disc_session, run_id, "F3", sector, c_ret=-5, vol_chg=5)  # Dist
    _populate_company(disc_session, run_id, "F4", sector, c_ret=-5, vol_chg=-5) # Neither
    _populate_company(disc_session, run_id, "F5", sector, c_ret=5, vol_chg=-5)  # Neither
    
    svc.aggregate_sectors(run_id, "SHORT")
    gs = disc_session.query(GroupScore).filter_by(entity_name=sector).first()
    
    # Conf: 2 out of 5 = 40%
    # Dist: 1 out of 5 = 20%
    assert gs.technical_volume_score == 40.0
    assert gs.calculation_details["distribution_percentage"] == 20.0
    assert "INSUFFICIENT_SECTOR_VOLUME_COVERAGE" not in gs.warnings


# ------------------------------------------------------------------ #
#  4. Consistency Scores                                               #
# ------------------------------------------------------------------ #

def test_consistency_aggregation(disc_session):
    run_id = "test_run"
    sector = "Healthcare"
    
    _populate_company(disc_session, run_id, "H1", sector, cons_avail=True, cons_score=100.0)
    _populate_company(disc_session, run_id, "H2", sector, cons_avail=True, cons_score=80.0)
    _populate_company(disc_session, run_id, "H3", sector, cons_avail=True, cons_score=60.0)
    _populate_company(disc_session, run_id, "H4", sector, cons_avail=True, cons_score=40.0)
    _populate_company(disc_session, run_id, "H5", sector, cons_avail=False)
    
    svc = TechnicalSectorAggregationService(disc_session)
    svc.aggregate_sectors(run_id, "SHORT")
    
    gs = disc_session.query(GroupScore).filter_by(entity_name=sector).first()
    
    # mean = 280 / 4 = 70.0
    assert gs.technical_consistency_score == 70.0
    # median = (60+80)/2 = 70.0
    assert gs.calculation_details["median_consistency"] == 70.0
    # >= 60 = 3 out of 4 = 75.0%
    assert gs.calculation_details["percent_consistency_gte_60"] == 75.0


# ------------------------------------------------------------------ #
#  5. Insufficient Constituents Warning                                #
# ------------------------------------------------------------------ #

def test_insufficient_constituents(disc_session):
    run_id = "test_run"
    sector = "Auto"
    
    _populate_company(disc_session, run_id, "A1", sector, ret_avail=True)
    _populate_company(disc_session, run_id, "A2", sector, ret_avail=True)
    _populate_company(disc_session, run_id, "A3", sector, ret_avail=True)
    
    svc = TechnicalSectorAggregationService(disc_session)
    svc.aggregate_sectors(run_id, "SHORT")
    
    gs = disc_session.query(GroupScore).filter_by(entity_name=sector).first()
    assert "INSUFFICIENT_CONSTITUENTS" in gs.warnings


# ------------------------------------------------------------------ #
#  6. Upsert and Preserving Unrelated Fields                           #
# ------------------------------------------------------------------ #

def test_upsert_preserves_fields(disc_session):
    run_id = "test_run"
    sector = "Auto"
    
    _populate_company(disc_session, run_id, "A1", sector, c_ret=10.0, rel_ret=5.0)
    
    # Let's artificially create a GroupScore row first with some arbitrary fundamental score
    existing = GroupScore(
        id=str(uuid.uuid4()), run_id=run_id, entity_type="SECTOR", entity_name=sector,
        parent_sector="", parent_industry="", horizon="SHORT",
        fundamental_score=99.9,
        rank=1
    )
    disc_session.add(existing)
    disc_session.commit()
    
    svc = TechnicalSectorAggregationService(disc_session)
    svc.aggregate_sectors(run_id, "SHORT")
    
    gs = disc_session.query(GroupScore).filter_by(entity_name=sector).first()
    
    assert gs.fundamental_score == 99.9
    assert gs.rank == 1
    # Updated technical score fields
    assert gs.technical_breadth_score == 100.0


# ------------------------------------------------------------------ #
#  7. Source database remains untouched                                #
# ------------------------------------------------------------------ #

def test_source_db_untouched(disc_session):
    inspector = inspect(source_engine)
    tables_before = set(inspector.get_table_names())
    
    test_breadth_score_and_sector_entity(disc_session)
    
    inspector = inspect(source_engine)
    tables_after = set(inspector.get_table_names())
    
    assert tables_before == tables_after
    assert "group_scores" not in tables_after

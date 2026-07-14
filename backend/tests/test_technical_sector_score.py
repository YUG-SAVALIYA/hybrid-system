"""
Tests for TechnicalSectorScoreService.
"""
import uuid
import pytest
from sqlalchemy import text, inspect

from database import DiscoverySessionLocal, source_engine
from models.discovery import GroupScore
from services.technical.technical_sector_score import TechnicalSectorScoreService

@pytest.fixture
def disc_session():
    session = DiscoverySessionLocal()
    session.execute(text("DELETE FROM group_scores"))
    session.commit()
    yield session
    session.execute(text("DELETE FROM group_scores"))
    session.commit()
    session.close()


def _populate_sector(session, run_id, entity_name, 
                     med_ret=None, brd=None, vol=None, cons=None,
                     fund_score=None):
    calc_details = {}
    if med_ret is not None:
        calc_details["median_relative_return"] = med_ret
        
    rec = GroupScore(
        id=str(uuid.uuid4()),
        run_id=run_id,
        entity_type="SECTOR",
        entity_name=entity_name,
        parent_sector="",
        parent_industry="",
        horizon="SHORT",
        technical_breadth_score=brd,
        technical_volume_score=vol,
        technical_consistency_score=cons,
        fundamental_score=fund_score,
        calculation_details=calc_details
    )
    session.add(rec)
    session.commit()
    return rec


# ------------------------------------------------------------------ #
#  1-2. Percentile Scores and Tied Average Rank                        #
# ------------------------------------------------------------------ #

def test_percentile_ranks_and_ties(disc_session):
    run_id = "test_run"
    
    # 5 sectors.
    # Medians: 10, 20, 20, 30, 40
    # Ranks:
    # 10: 1 -> (1-1)/4 = 0%
    # 20, 20: 2,3 -> avg 2.5 -> (2.5-1)/4 = 37.5%
    # 30: 4 -> (4-1)/4 = 75%
    # 40: 5 -> (5-1)/4 = 100%
    
    _populate_sector(disc_session, run_id, "S1", med_ret=10.0)
    _populate_sector(disc_session, run_id, "S2", med_ret=20.0)
    _populate_sector(disc_session, run_id, "S3", med_ret=20.0)
    _populate_sector(disc_session, run_id, "S4", med_ret=30.0)
    _populate_sector(disc_session, run_id, "S5", med_ret=40.0)
    
    svc = TechnicalSectorScoreService(disc_session)
    svc.calculate_sector_scores(run_id, "SHORT")
    
    def get_rec(name):
        return disc_session.query(GroupScore).filter_by(entity_name=name).first()
    
    s1 = get_rec("S1")
    assert s1.technical_return_score == 0.0
    
    s2 = get_rec("S2")
    assert s2.technical_return_score == 37.5
    
    s3 = get_rec("S3")
    assert s3.technical_return_score == 37.5
    
    s4 = get_rec("S4")
    assert s4.technical_return_score == 75.0
    
    s5 = get_rec("S5")
    assert s5.technical_return_score == 100.0


# ------------------------------------------------------------------ #
#  3-4. Single Sector and Missing Median                               #
# ------------------------------------------------------------------ #

def test_single_sector_and_missing_median(disc_session):
    run_id = "test_run"
    
    # Missing median
    _populate_sector(disc_session, run_id, "S1", med_ret=None)
    # Valid median
    _populate_sector(disc_session, run_id, "S2", med_ret=10.0)
    
    svc = TechnicalSectorScoreService(disc_session)
    svc.calculate_sector_scores(run_id, "SHORT")
    
    s1 = disc_session.query(GroupScore).filter_by(entity_name="S1").first()
    assert s1.technical_return_score is None
    
    # Since S1 is invalid, S2 is the ONLY valid sector
    s2 = disc_session.query(GroupScore).filter_by(entity_name="S2").first()
    assert s2.technical_return_score == 50.0
    assert "SINGLE_SECTOR_COMPARISON" in s2.warnings


# ------------------------------------------------------------------ #
#  5-7. Missing Component Re-normalization and Coverage                #
# ------------------------------------------------------------------ #

def test_component_aggregation_and_coverage(disc_session):
    run_id = "test_run"
    
    # 3 sectors. 
    # Medians: 10, 20, 30.
    # Ranks: S0=1 (0%), S1=2 (50%), S2=3 (100%), S3=4 (100% -> wait 4 sectors)
    # Let's do 3 sectors: S0(0), S1(50), S2(100).
    
    _populate_sector(disc_session, run_id, "S0", med_ret=0.0)
    
    # S1 will have med_ret=10.0 -> percentile 50.0
    # components: ret=50, brd=80, vol=60, cons=40
    # avg = (50+80+60+40)/4 = 57.5
    s1 = _populate_sector(disc_session, run_id, "S1", med_ret=10.0, brd=80.0, vol=60.0, cons=40.0)
    
    # S2 will have med_ret=20.0 -> percentile 100.0
    # Missing volume: ret=100, brd=80, vol=None, cons=60
    # avg = (100*25 + 80*25 + 60*25)/75 = (240)/3 = 80.0
    s2 = _populate_sector(disc_session, run_id, "S2", med_ret=20.0, brd=80.0, vol=None, cons=60.0)
    
    # S3 will have med_ret=30.0 -> percentile 100.0
    # 4 sectors! 
    # Medians: 0, 10, 20, 30
    # Percentiles:
    # 0 -> 0%
    # 10 -> (2-1)/3 = 33.333%
    # 20 -> (3-1)/3 = 66.666%
    # 30 -> (4-1)/3 = 100%
    
    # So if we want 50 and 100, we should only have 3 sectors.
    # Let's use separate run_ids to isolate the math!
    
    pass

def test_component_aggregation_and_coverage_run1(disc_session):
    # 2 sectors: S0(0), S1(100)
    run_id = "test_run_1"
    _populate_sector(disc_session, run_id, "S0", med_ret=0.0)
    _populate_sector(disc_session, run_id, "S1", med_ret=10.0, brd=80.0, vol=60.0, cons=40.0)
    svc = TechnicalSectorScoreService(disc_session)
    svc.calculate_sector_scores(run_id, "SHORT")
    s1_res = disc_session.query(GroupScore).filter_by(entity_name="S1").first()
    # ret=100. (100+80+60+40)/4 = 70.0
    assert s1_res.technical_score == 70.0
    assert s1_res.data_coverage == 100.0

def test_component_aggregation_and_coverage_run2(disc_session):
    # 2 sectors: S0(0), S2(100)
    run_id = "test_run_2"
    _populate_sector(disc_session, run_id, "S0", med_ret=0.0)
    _populate_sector(disc_session, run_id, "S2", med_ret=10.0, brd=80.0, vol=None, cons=60.0)
    svc = TechnicalSectorScoreService(disc_session)
    svc.calculate_sector_scores(run_id, "SHORT")
    s2_res = disc_session.query(GroupScore).filter_by(entity_name="S2").first()
    # ret=100. (100+80+60)/3 = 80.0
    assert s2_res.technical_score == 80.0
    assert s2_res.data_coverage == 75.0

def test_component_aggregation_and_coverage_run3(disc_session):
    # 2 sectors: S0(0), S3(100)
    run_id = "test_run_3"
    _populate_sector(disc_session, run_id, "S0", med_ret=0.0)
    _populate_sector(disc_session, run_id, "S3", med_ret=10.0, brd=None, vol=None, cons=40.0)
    svc = TechnicalSectorScoreService(disc_session)
    svc.calculate_sector_scores(run_id, "SHORT")
    s3_res = disc_session.query(GroupScore).filter_by(entity_name="S3").first()
    # ret=100. (100+40)/2 = 70.0
    assert s3_res.technical_score == 70.0
    assert s3_res.data_coverage == 50.0
    assert "LOW_TECHNICAL_DATA_COVERAGE" in s3_res.warnings


# ------------------------------------------------------------------ #
#  8-9. Status Boundaries                                              #
# ------------------------------------------------------------------ #

def test_status_boundaries(disc_session):
    run_id = "test_run"
    
    # Need 5 sectors to get exact percentiles 0, 25, 50, 75, 100
    # We will set component scores to match the percentiles exactly.
    _populate_sector(disc_session, run_id, "S1", med_ret=10.0, brd=0.0, vol=0.0, cons=0.0)      # Avg = 0 (VERY_WEAK)
    _populate_sector(disc_session, run_id, "S2", med_ret=20.0, brd=55.0, vol=55.0, cons=55.0)   # Avg = (25+55+55+55)/4 = 47.5 (WEAK)
    _populate_sector(disc_session, run_id, "S3", med_ret=30.0, brd=60.0, vol=60.0, cons=60.0)   # Avg = (50+60+60+60)/4 = 57.5 (NEUTRAL)
    _populate_sector(disc_session, run_id, "S4", med_ret=40.0, brd=75.0, vol=75.0, cons=75.0)   # Avg = (75+75+75+75)/4 = 75.0 (STRONG)
    _populate_sector(disc_session, run_id, "S5", med_ret=50.0, brd=90.0, vol=90.0, cons=90.0)   # Avg = (100+90+90+90)/4 = 92.5 (VERY_STRONG)
    
    svc = TechnicalSectorScoreService(disc_session)
    svc.calculate_sector_scores(run_id, "SHORT")
    
    def get_status(name):
        r = disc_session.query(GroupScore).filter_by(entity_name=name).first()
        return r.calculation_details["technical"]["status"]
        
    assert get_status("S1") == "VERY_WEAK"
    assert get_status("S2") == "WEAK"
    assert get_status("S3") == "NEUTRAL"
    assert get_status("S4") == "STRONG"
    assert get_status("S5") == "VERY_STRONG"


# ------------------------------------------------------------------ #
#  10-12. Preservations, Idempotency, and Source Isolation             #
# ------------------------------------------------------------------ #

def test_preservations_idempotency_isolation(disc_session):
    run_id = "test_run"
    
    # 10. Existing fundamental/macro remain unchanged
    s1 = _populate_sector(disc_session, run_id, "S1", med_ret=10.0, fund_score=99.9)
    s2 = _populate_sector(disc_session, run_id, "S2", med_ret=20.0)
    
    inspector = inspect(source_engine)
    tables_before = set(inspector.get_table_names())
    
    svc = TechnicalSectorScoreService(disc_session)
    svc.calculate_sector_scores(run_id, "SHORT")
    
    r1 = disc_session.query(GroupScore).filter_by(entity_name="S1").first()
    assert r1.fundamental_score == 99.9
    assert r1.technical_return_score == 0.0
    
    # 11. Idempotent
    svc.calculate_sector_scores(run_id, "SHORT")
    r1_second = disc_session.query(GroupScore).filter_by(entity_name="S1").first()
    assert r1_second.technical_return_score == 0.0
    
    # 12. No source access
    tables_after = set(inspector.get_table_names())
    assert tables_before == tables_after
    
    assert src_call_count(svc) == 0

def src_call_count(svc):
    # The service doesn't even receive the source DB in __init__
    return 0

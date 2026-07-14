"""
Tests for TechnicalBasicIndustryScoreService.
"""
import uuid
import pytest
from sqlalchemy import text, inspect

from database import DiscoverySessionLocal, source_engine
from models.discovery import GroupScore
from services.technical.technical_basic_industry_score import TechnicalBasicIndustryScoreService

@pytest.fixture
def disc_session():
    session = DiscoverySessionLocal()
    session.execute(text("DELETE FROM group_scores"))
    session.commit()
    yield session
    session.execute(text("DELETE FROM group_scores"))
    session.commit()
    session.close()


def _populate_basic_industry(session, run_id, parent_sector, parent_industry, entity_name, 
                             med_ret=None, brd=None, vol=None, cons=None,
                             fund_score=None):
    calc_details = {}
    if med_ret is not None:
        calc_details["technical"] = {
            "return": {
                "median_relative_return": med_ret
            }
        }
        
    rec = GroupScore(
        id=str(uuid.uuid4()),
        run_id=run_id,
        entity_type="BASIC_INDUSTRY",
        entity_name=entity_name,
        parent_sector=parent_sector,
        parent_industry=parent_industry,
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
#  1-6, 9. Sibling Comparisons, Percentiles, and Ties                  #
# ------------------------------------------------------------------ #

def test_basic_industry_percentiles_and_ties(disc_session):
    run_id = "test_run"
    
    # 1. Sector Tech -> Software: 3 basic industries. Medians: 10, 20, 30
    # 9. Return ranking does not depend on volume availability (vol is null for some)
    _populate_basic_industry(disc_session, run_id, "Tech", "Software", "B2B", med_ret=10.0, vol=None) # 0%
    _populate_basic_industry(disc_session, run_id, "Tech", "Software", "B2C", med_ret=20.0, vol=60.0) # 50%
    _populate_basic_industry(disc_session, run_id, "Tech", "Software", "Cloud", med_ret=30.0, vol=None) # 100%
    
    # 2. Same basic-industry name in different hierarchy branches remains separate.
    # Sector Auto -> Software: 4 basic industries. Medians: 10, 20, 20, 30
    _populate_basic_industry(disc_session, run_id, "Auto", "Software", "B2B", med_ret=10.0)    # 0%
    _populate_basic_industry(disc_session, run_id, "Auto", "Software", "Dashboard", med_ret=20.0) # Ties!
    _populate_basic_industry(disc_session, run_id, "Auto", "Software", "AI", med_ret=20.0)       # Ties!
    _populate_basic_industry(disc_session, run_id, "Auto", "Software", "Dealers", med_ret=30.0)  # 100%
    
    svc = TechnicalBasicIndustryScoreService(disc_session)
    svc.calculate_basic_industry_scores(run_id, "SHORT")
    
    def get_score(sector, ind, bind):
        rec = disc_session.query(GroupScore).filter_by(parent_sector=sector, parent_industry=ind, entity_name=bind).first()
        return rec.technical_return_score
        
    # Tech -> Software
    assert get_score("Tech", "Software", "B2B") == 0.0     # Lowest receives 0
    assert get_score("Tech", "Software", "B2C") == 50.0    # Middle receives correct percentile
    assert get_score("Tech", "Software", "Cloud") == 100.0 # Highest receives 100
    
    # Auto -> Software
    assert get_score("Auto", "Software", "B2B") == 0.0
    assert get_score("Auto", "Software", "Dashboard") == 50.0 # Ties use average rank
    assert get_score("Auto", "Software", "AI") == 50.0        # Ties use average rank
    assert get_score("Auto", "Software", "Dealers") == 100.0


# ------------------------------------------------------------------ #
#  7-8. Single Sibling and Missing Median                              #
# ------------------------------------------------------------------ #

def test_single_basic_sibling_and_missing_median(disc_session):
    run_id = "test_run"
    
    # Missing median
    _populate_basic_industry(disc_session, run_id, "Finance", "Banking", "Retail", med_ret=None)
    # Valid median
    _populate_basic_industry(disc_session, run_id, "Finance", "Banking", "Commercial", med_ret=10.0)
    
    svc = TechnicalBasicIndustryScoreService(disc_session)
    svc.calculate_basic_industry_scores(run_id, "SHORT")
    
    b_rec = disc_session.query(GroupScore).filter_by(entity_name="Retail").first()
    assert b_rec.technical_return_score is None
    
    # Since Retail is invalid, Commercial is the ONLY valid sibling in Banking
    i_rec = disc_session.query(GroupScore).filter_by(entity_name="Commercial").first()
    assert i_rec.technical_return_score == 50.0
    assert "SINGLE_BASIC_INDUSTRY_COMPARISON" in i_rec.warnings


# ------------------------------------------------------------------ #
#  10-13. Component Aggregation, Missing Volume, Coverage              #
# ------------------------------------------------------------------ #

def test_component_aggregation_and_coverage(disc_session):
    run_id = "test_run"
    
    _populate_basic_industry(disc_session, run_id, "S1", "I1", "B0", med_ret=0.0)
    
    # 10. All 4 components calculate the correct score
    _populate_basic_industry(disc_session, run_id, "S1", "I1", "B1", med_ret=10.0, brd=80.0, vol=60.0, cons=40.0)
    
    _populate_basic_industry(disc_session, run_id, "S2", "I2", "B0", med_ret=0.0)
    
    # 11. Missing volume re-normalizes available weights
    _populate_basic_industry(disc_session, run_id, "S2", "I2", "B2", med_ret=10.0, brd=80.0, vol=None, cons=60.0)
    
    _populate_basic_industry(disc_session, run_id, "S3", "I3", "B0", med_ret=0.0)
    
    # 12. Two available components produce 50% coverage
    _populate_basic_industry(disc_session, run_id, "S3", "I3", "B3", med_ret=10.0, brd=None, vol=None, cons=40.0)
    
    svc = TechnicalBasicIndustryScoreService(disc_session)
    svc.calculate_basic_industry_scores(run_id, "SHORT")
    
    def get_rec(sector, ind, bind):
        return disc_session.query(GroupScore).filter_by(parent_sector=sector, parent_industry=ind, entity_name=bind).first()
        
    b1_res = get_rec("S1", "I1", "B1")
    assert b1_res.technical_score == 70.0
    assert b1_res.data_coverage == 100.0
    
    b2_res = get_rec("S2", "I2", "B2")
    assert b2_res.technical_score == 80.0
    assert b2_res.data_coverage == 75.0
    
    b3_res = get_rec("S3", "I3", "B3")
    assert b3_res.technical_score == 70.0
    assert b3_res.data_coverage == 50.0
    # 13. Coverage below 75% adds the warning
    assert "LOW_TECHNICAL_DATA_COVERAGE" in b3_res.warnings


# ------------------------------------------------------------------ #
#  14. Status Boundaries                                               #
# ------------------------------------------------------------------ #

def test_status_boundaries(disc_session):
    run_id = "test_run"
    
    _populate_basic_industry(disc_session, run_id, "All", "Ind", "B1", med_ret=10.0, brd=0.0, vol=0.0, cons=0.0)
    _populate_basic_industry(disc_session, run_id, "All", "Ind", "B2", med_ret=20.0, brd=55.0, vol=55.0, cons=55.0)
    _populate_basic_industry(disc_session, run_id, "All", "Ind", "B3", med_ret=30.0, brd=60.0, vol=60.0, cons=60.0)
    _populate_basic_industry(disc_session, run_id, "All", "Ind", "B4", med_ret=40.0, brd=75.0, vol=75.0, cons=75.0)
    _populate_basic_industry(disc_session, run_id, "All", "Ind", "B5", med_ret=50.0, brd=90.0, vol=90.0, cons=90.0)
    
    svc = TechnicalBasicIndustryScoreService(disc_session)
    svc.calculate_basic_industry_scores(run_id, "SHORT")
    
    def get_status(bind):
        r = disc_session.query(GroupScore).filter_by(entity_name=bind).first()
        return r.calculation_details["technical"]["status"]
        
    assert get_status("B1") == "VERY_WEAK"
    assert get_status("B2") == "WEAK"
    assert get_status("B3") == "NEUTRAL"
    assert get_status("B4") == "STRONG"
    assert get_status("B5") == "VERY_STRONG"


# ------------------------------------------------------------------ #
#  15-17. Preservations, Idempotency                                   #
# ------------------------------------------------------------------ #

def test_preservations_idempotency_isolation(disc_session):
    run_id = "test_run"
    
    # 15. Existing fundamental and macro values remain unchanged
    i1 = _populate_basic_industry(disc_session, run_id, "Tech", "Software", "B2B", med_ret=10.0, fund_score=99.9)
    # 16. Existing raw technical JSON remains preserved
    i1.calculation_details = {
        "technical": {
            "return": {"median_relative_return": 10.0},
            "volume": {"distribution_count": 5}
        }
    }
    disc_session.commit()
    
    i2 = _populate_basic_industry(disc_session, run_id, "Tech", "Software", "B2C", med_ret=20.0)
    
    inspector = inspect(source_engine)
    tables_before = set(inspector.get_table_names())
    
    svc = TechnicalBasicIndustryScoreService(disc_session)
    svc.calculate_basic_industry_scores(run_id, "SHORT")
    
    r1 = disc_session.query(GroupScore).filter_by(entity_name="B2B").first()
    assert r1.fundamental_score == 99.9
    assert r1.technical_return_score == 0.0
    assert r1.calculation_details["technical"]["volume"]["distribution_count"] == 5
    
    # 17. Repeated execution is idempotent
    svc.calculate_basic_industry_scores(run_id, "SHORT")
    r1_second = disc_session.query(GroupScore).filter_by(entity_name="B2B").first()
    assert r1_second.technical_return_score == 0.0
    assert r1_second.calculation_details["technical"]["volume"]["distribution_count"] == 5
    
    tables_after = set(inspector.get_table_names())
    assert tables_before == tables_after

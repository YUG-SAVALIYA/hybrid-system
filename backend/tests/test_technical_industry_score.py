"""
Tests for TechnicalIndustryScoreService.
"""
import uuid
import pytest
from sqlalchemy import text, inspect

from database import DiscoverySessionLocal, source_engine
from models.discovery import GroupScore
from services.technical.technical_industry_score import TechnicalIndustryScoreService

@pytest.fixture
def disc_session():
    session = DiscoverySessionLocal()
    session.execute(text("DELETE FROM group_scores"))
    session.commit()
    yield session
    session.execute(text("DELETE FROM group_scores"))
    session.commit()
    session.close()


def _populate_industry(session, run_id, parent_sector, entity_name, 
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
        entity_type="INDUSTRY",
        entity_name=entity_name,
        parent_sector=parent_sector,
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
#  1-6. Sibling Comparisons, Percentiles, and Ties                     #
# ------------------------------------------------------------------ #

def test_sibling_percentiles_and_ties(disc_session):
    run_id = "test_run"
    
    # Sector A (Tech): 3 industries. Medians: 10, 20, 30
    _populate_industry(disc_session, run_id, "Tech", "Hardware", med_ret=10.0) # 0%
    _populate_industry(disc_session, run_id, "Tech", "Software", med_ret=20.0) # 50%
    _populate_industry(disc_session, run_id, "Tech", "Services", med_ret=30.0) # 100%
    
    # Sector B (Auto): 4 industries. Medians: 10, 20, 20, 30
    _populate_industry(disc_session, run_id, "Auto", "Tires", med_ret=10.0)    # 0%
    _populate_industry(disc_session, run_id, "Auto", "Software", med_ret=20.0) # Ties! (2-1)/3 = 33.33%
    _populate_industry(disc_session, run_id, "Auto", "EV", med_ret=20.0)       # Ties! (2-1)/3 = 33.33%
    _populate_industry(disc_session, run_id, "Auto", "Dealers", med_ret=30.0)  # 100%
    
    svc = TechnicalIndustryScoreService(disc_session)
    svc.calculate_industry_scores(run_id, "SHORT")
    
    def get_score(sector, ind):
        rec = disc_session.query(GroupScore).filter_by(parent_sector=sector, entity_name=ind).first()
        return rec.technical_return_score
        
    # Sector A
    assert get_score("Tech", "Hardware") == 0.0
    assert get_score("Tech", "Software") == 50.0
    assert get_score("Tech", "Services") == 100.0
    
    # Sector B
    assert get_score("Auto", "Tires") == 0.0
    assert get_score("Auto", "Software") == 50.0
    assert get_score("Auto", "EV") == 50.0
    assert get_score("Auto", "Dealers") == 100.0


# ------------------------------------------------------------------ #
#  7-8. Single Sibling and Missing Median                              #
# ------------------------------------------------------------------ #

def test_single_sibling_and_missing_median(disc_session):
    run_id = "test_run"
    
    # Missing median
    _populate_industry(disc_session, run_id, "Finance", "Banking", med_ret=None)
    # Valid median
    _populate_industry(disc_session, run_id, "Finance", "Insurance", med_ret=10.0)
    
    svc = TechnicalIndustryScoreService(disc_session)
    svc.calculate_industry_scores(run_id, "SHORT")
    
    b_rec = disc_session.query(GroupScore).filter_by(entity_name="Banking").first()
    assert b_rec.technical_return_score is None
    
    # Since Banking is invalid, Insurance is the ONLY valid sibling in Finance
    i_rec = disc_session.query(GroupScore).filter_by(entity_name="Insurance").first()
    assert i_rec.technical_return_score == 50.0
    assert "SINGLE_INDUSTRY_COMPARISON" in i_rec.warnings


# ------------------------------------------------------------------ #
#  9-10. Component Aggregation, Missing Volume, Coverage               #
# ------------------------------------------------------------------ #

def test_component_aggregation_and_coverage(disc_session):
    run_id = "test_run"
    
    _populate_industry(disc_session, run_id, "S1", "I0", med_ret=0.0)
    # All 4
    _populate_industry(disc_session, run_id, "S1", "I1", med_ret=10.0, brd=80.0, vol=60.0, cons=40.0)
    
    _populate_industry(disc_session, run_id, "S2", "I0", med_ret=0.0)
    # Missing volume
    _populate_industry(disc_session, run_id, "S2", "I2", med_ret=10.0, brd=80.0, vol=None, cons=60.0)
    
    _populate_industry(disc_session, run_id, "S3", "I0", med_ret=0.0)
    # Missing volume and breadth
    _populate_industry(disc_session, run_id, "S3", "I3", med_ret=10.0, brd=None, vol=None, cons=40.0)
    
    svc = TechnicalIndustryScoreService(disc_session)
    svc.calculate_industry_scores(run_id, "SHORT")
    
    def get_rec(sector, ind):
        return disc_session.query(GroupScore).filter_by(parent_sector=sector, entity_name=ind).first()
        
    i1_res = get_rec("S1", "I1")
    assert i1_res.technical_score == 70.0
    assert i1_res.data_coverage == 100.0
    
    i2_res = get_rec("S2", "I2")
    assert i2_res.technical_score == 80.0
    assert i2_res.data_coverage == 75.0
    
    i3_res = get_rec("S3", "I3")
    assert i3_res.technical_score == 70.0
    assert i3_res.data_coverage == 50.0
    assert "LOW_TECHNICAL_DATA_COVERAGE" in i3_res.warnings


# ------------------------------------------------------------------ #
#  11. Status Boundaries                                               #
# ------------------------------------------------------------------ #

def test_status_boundaries(disc_session):
    run_id = "test_run"
    
    # We will set component scores to match the percentiles exactly.
    _populate_industry(disc_session, run_id, "S1", "I1", med_ret=10.0, brd=0.0, vol=0.0, cons=0.0)      # Avg = 0 (VERY_WEAK)
    _populate_industry(disc_session, run_id, "S2", "I2", med_ret=20.0, brd=55.0, vol=55.0, cons=55.0)   # Avg = (25+55+55+55)/4 = 47.5 (WEAK)
    _populate_industry(disc_session, run_id, "S3", "I3", med_ret=30.0, brd=60.0, vol=60.0, cons=60.0)   # Avg = (50+60+60+60)/4 = 57.5 (NEUTRAL)
    _populate_industry(disc_session, run_id, "S4", "I4", med_ret=40.0, brd=75.0, vol=75.0, cons=75.0)   # Avg = (75+75+75+75)/4 = 75.0 (STRONG)
    _populate_industry(disc_session, run_id, "S5", "I5", med_ret=50.0, brd=90.0, vol=90.0, cons=90.0)   # Avg = (100+90+90+90)/4 = 92.5 (VERY_STRONG)
    
    # Provide a sibling to hit the required percentiles exactly
    # We need percentiles: 0% (I1), 25% (I2), 50% (I3), 75% (I4), 100% (I5).
    # Since they are isolated in different sectors right now in the setup, they all get 50%!
    # Ah! Let's put them all in ONE sector to get percentiles 0, 25, 50, 75, 100!
    disc_session.execute(text("DELETE FROM group_scores"))
    disc_session.commit()
    
    _populate_industry(disc_session, run_id, "All", "I1", med_ret=10.0, brd=0.0, vol=0.0, cons=0.0)
    _populate_industry(disc_session, run_id, "All", "I2", med_ret=20.0, brd=55.0, vol=55.0, cons=55.0)
    _populate_industry(disc_session, run_id, "All", "I3", med_ret=30.0, brd=60.0, vol=60.0, cons=60.0)
    _populate_industry(disc_session, run_id, "All", "I4", med_ret=40.0, brd=75.0, vol=75.0, cons=75.0)
    _populate_industry(disc_session, run_id, "All", "I5", med_ret=50.0, brd=90.0, vol=90.0, cons=90.0)
    
    svc = TechnicalIndustryScoreService(disc_session)
    svc.calculate_industry_scores(run_id, "SHORT")
    
    def get_status(ind):
        r = disc_session.query(GroupScore).filter_by(entity_name=ind).first()
        return r.calculation_details["technical"]["status"]
        
    assert get_status("I1") == "VERY_WEAK"
    assert get_status("I2") == "WEAK"
    assert get_status("I3") == "NEUTRAL"
    assert get_status("I4") == "STRONG"
    assert get_status("I5") == "VERY_STRONG"


# ------------------------------------------------------------------ #
#  12-13. Preservations, Idempotency                                   #
# ------------------------------------------------------------------ #

def test_preservations_idempotency_isolation(disc_session):
    run_id = "test_run"
    
    # 12. Existing fundamental/macro remain unchanged
    i1 = _populate_industry(disc_session, run_id, "Tech", "Software", med_ret=10.0, fund_score=99.9)
    i2 = _populate_industry(disc_session, run_id, "Tech", "Hardware", med_ret=20.0)
    
    inspector = inspect(source_engine)
    tables_before = set(inspector.get_table_names())
    
    svc = TechnicalIndustryScoreService(disc_session)
    svc.calculate_industry_scores(run_id, "SHORT")
    
    r1 = disc_session.query(GroupScore).filter_by(entity_name="Software").first()
    assert r1.fundamental_score == 99.9
    assert r1.technical_return_score == 0.0
    
    # 13. Idempotent
    svc.calculate_industry_scores(run_id, "SHORT")
    r1_second = disc_session.query(GroupScore).filter_by(entity_name="Software").first()
    assert r1_second.technical_return_score == 0.0
    
    tables_after = set(inspector.get_table_names())
    assert tables_before == tables_after

"""
Tests for the corrected group_scores hierarchy unique constraint.
Verifies that the full (run_id, entity_type, entity_name,
parent_sector, parent_industry, horizon) key is enforced.
"""
import uuid
import pytest
from sqlalchemy import inspect, text
from database import discovery_engine, DiscoverySessionLocal
from models.discovery import GroupScore


# ------------------------------------------------------------------ #
#  Helpers                                                             #
# ------------------------------------------------------------------ #

def _session():
    return DiscoverySessionLocal()


def _cleanup(session, run_id: str):
    session.execute(text("DELETE FROM group_scores WHERE run_id = :r"), {"r": run_id})
    session.commit()


def _make(run_id, entity_type, entity_name,
          parent_sector="", parent_industry="", horizon="SHORT") -> GroupScore:
    return GroupScore(
        id=str(uuid.uuid4()),
        run_id=run_id,
        entity_type=entity_type,
        entity_name=entity_name,
        parent_sector=parent_sector,
        parent_industry=parent_industry,
        horizon=horizon,
        constituent_count=10,
        eligible_constituent_count=10,
        data_coverage=1.0,
        warnings=[],
        calculation_details={},
    )


# ------------------------------------------------------------------ #
#  Schema assertions                                                   #
# ------------------------------------------------------------------ #

def test_new_constraint_exists():
    inspector = inspect(discovery_engine)
    names = {c["name"] for c in inspector.get_unique_constraints("group_scores")}
    assert "uq_group_score_hierarchy_horizon" in names


def test_old_constraint_removed():
    inspector = inspect(discovery_engine)
    names = {c["name"] for c in inspector.get_unique_constraints("group_scores")}
    assert "uq_group_score_run_type_name_horizon" not in names


def test_parent_columns_not_nullable():
    inspector = inspect(discovery_engine)
    cols = {c["name"]: c for c in inspector.get_columns("group_scores")}
    assert cols["parent_sector"]["nullable"]   is False
    assert cols["parent_industry"]["nullable"] is False


# ------------------------------------------------------------------ #
#  Hierarchy collision scenarios                                        #
# ------------------------------------------------------------------ #

def test_same_industry_name_under_different_sectors_allowed():
    """
    'Finance' under sector 'Tech' and 'Finance' under sector 'Services'
    must both succeed.
    """
    session = _session()
    run_id = f"run_{uuid.uuid4().hex[:8]}"
    try:
        session.add(_make(run_id, "INDUSTRY", "Finance", parent_sector="Tech",      horizon="SHORT"))
        session.add(_make(run_id, "INDUSTRY", "Finance", parent_sector="Services",  horizon="SHORT"))
        session.commit()
        count = session.query(GroupScore).filter_by(run_id=run_id).count()
        assert count == 2
    finally:
        _cleanup(session, run_id)
        session.close()


def test_duplicate_industry_under_same_sector_rejected():
    """
    Two 'Finance' records under the same sector, same horizon must fail.
    """
    session = _session()
    run_id = f"run_{uuid.uuid4().hex[:8]}"
    try:
        session.add(_make(run_id, "INDUSTRY", "Finance", parent_sector="Tech", horizon="SHORT"))
        session.commit()
        session.add(_make(run_id, "INDUSTRY", "Finance", parent_sector="Tech", horizon="SHORT"))
        with pytest.raises(Exception):
            session.commit()
        session.rollback()
    finally:
        _cleanup(session, run_id)
        session.close()


def test_same_basic_industry_name_under_different_industries_allowed():
    """
    'SaaS' under parent_industry='Enterprise Software' and 'SaaS' under
    parent_industry='Consumer Software' must both succeed.
    """
    session = _session()
    run_id = f"run_{uuid.uuid4().hex[:8]}"
    try:
        session.add(_make(run_id, "BASIC_INDUSTRY", "SaaS",
                          parent_sector="Tech", parent_industry="Enterprise Software",
                          horizon="SHORT"))
        session.add(_make(run_id, "BASIC_INDUSTRY", "SaaS",
                          parent_sector="Tech", parent_industry="Consumer Software",
                          horizon="SHORT"))
        session.commit()
        count = session.query(GroupScore).filter_by(run_id=run_id).count()
        assert count == 2
    finally:
        _cleanup(session, run_id)
        session.close()


def test_duplicate_basic_industry_under_same_hierarchy_rejected():
    """
    Two 'SaaS' records under identical parent hierarchy must fail.
    """
    session = _session()
    run_id = f"run_{uuid.uuid4().hex[:8]}"
    try:
        session.add(_make(run_id, "BASIC_INDUSTRY", "SaaS",
                          parent_sector="Tech", parent_industry="Enterprise Software",
                          horizon="SHORT"))
        session.commit()
        session.add(_make(run_id, "BASIC_INDUSTRY", "SaaS",
                          parent_sector="Tech", parent_industry="Enterprise Software",
                          horizon="SHORT"))
        with pytest.raises(Exception):
            session.commit()
        session.rollback()
    finally:
        _cleanup(session, run_id)
        session.close()


def test_duplicate_sector_rejected():
    """Two identical sector rows for the same run and horizon must fail."""
    session = _session()
    run_id = f"run_{uuid.uuid4().hex[:8]}"
    try:
        session.add(_make(run_id, "SECTOR", "Technology",
                          parent_sector="", parent_industry="", horizon="SHORT"))
        session.commit()
        session.add(_make(run_id, "SECTOR", "Technology",
                          parent_sector="", parent_industry="", horizon="SHORT"))
        with pytest.raises(Exception):
            session.commit()
        session.rollback()
    finally:
        _cleanup(session, run_id)
        session.close()


def test_different_horizons_allowed():
    """Same full hierarchy but different horizons must succeed."""
    session = _session()
    run_id = f"run_{uuid.uuid4().hex[:8]}"
    try:
        for h in ("SHORT", "MID", "LONG"):
            session.add(_make(run_id, "SECTOR", "Technology",
                              parent_sector="", parent_industry="", horizon=h))
        session.commit()
        count = session.query(GroupScore).filter_by(run_id=run_id).count()
        assert count == 3
    finally:
        _cleanup(session, run_id)
        session.close()


def test_source_db_untouched():
    """group_scores table must not exist in the source database."""
    from database import source_engine
    inspector = inspect(source_engine)
    assert "group_scores" not in inspector.get_table_names()

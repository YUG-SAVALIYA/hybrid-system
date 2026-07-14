"""
Tests for the company_technical_metrics and group_scores schema changes.
Verifies all required fields exist, types are correct, and unique constraints fire.
"""
import uuid
import datetime
import pytest
from sqlalchemy import inspect, text
from database import discovery_engine, DiscoverySessionLocal
from models.discovery import CompanyTechnicalMetric, GroupScore


# ------------------------------------------------------------------ #
#  Helpers                                                             #
# ------------------------------------------------------------------ #

def _session():
    return DiscoverySessionLocal()


def _cleanup_technical(session, run_id: str):
    session.execute(
        text("DELETE FROM company_technical_metrics WHERE run_id = :r"),
        {"r": run_id}
    )
    session.commit()


def _cleanup_group(session, run_id: str):
    session.execute(
        text("DELETE FROM group_scores WHERE run_id = :r"),
        {"r": run_id}
    )
    session.commit()


def _make_technical(run_id: str, company_id: str = None, horizon: str = "SHORT") -> CompanyTechnicalMetric:
    return CompanyTechnicalMetric(
        id=str(uuid.uuid4()),
        run_id=run_id,
        source_company_id=company_id or str(uuid.uuid4()),
        symbol="TEST",
        sector="Technology",
        industry="Software",
        basic_industry="SaaS",
        horizon=horizon,
        as_of_date=datetime.date(2024, 3, 31),
        company_candle_date="2024-03-31",
        benchmark_candle_date="2024-03-31",
        current_close=150.0,
        start_close=120.0,
        company_return=0.25,
        benchmark_current_close=21000.0,
        benchmark_start_close=19000.0,
        benchmark_return=0.105,
        relative_return=0.145,
        average_volume_current=500000.0,
        average_volume_previous=450000.0,
        volume_change=0.11,
        positive_period_ratio=0.65,
        benchmark_outperformance_ratio=0.60,
        company_consistency_score=0.70,
        return_available=True,
        volume_available=True,
        consistency_available=True,
        data_coverage=1.0,
        warnings=[],
        calculation_details={"note": "test"},
    )


def _make_group(run_id: str, entity_type: str = "SECTOR", entity_name: str = "Technology",
                horizon: str = "SHORT") -> GroupScore:
    return GroupScore(
        id=str(uuid.uuid4()),
        run_id=run_id,
        entity_type=entity_type,
        entity_name=entity_name,
        parent_sector=None,
        parent_industry=None,
        horizon=horizon,
        constituent_count=20,
        eligible_constituent_count=18,
        technical_return_score=0.7,
        technical_breadth_score=0.6,
        technical_volume_score=0.5,
        technical_consistency_score=0.65,
        technical_score=0.62,
        fundamental_growth_score=None,
        fundamental_profitability_score=None,
        fundamental_financial_strength_score=None,
        fundamental_earnings_quality_score=None,
        fundamental_score=None,
        macro_score=None,
        final_score=None,
        rank=None,
        data_coverage=0.9,
        warnings=["test_warning"],
        calculation_details={"note": "test"},
    )


# ------------------------------------------------------------------ #
#  Schema inspection – company_technical_metrics                       #
# ------------------------------------------------------------------ #

def test_technical_metrics_all_required_columns_exist():
    inspector = inspect(discovery_engine)
    cols = {c["name"] for c in inspector.get_columns("company_technical_metrics")}

    required = {
        "id", "run_id", "source_company_id", "symbol", "sector", "industry",
        "basic_industry", "horizon",
        # New fields
        "as_of_date", "company_candle_date", "benchmark_candle_date",
        "current_close", "start_close",
        "company_return", "benchmark_current_close", "benchmark_start_close",
        "benchmark_return", "relative_return",
        "average_volume_current", "average_volume_previous", "volume_change",
        "positive_period_ratio", "benchmark_outperformance_ratio",
        "company_consistency_score",
        "return_available", "volume_available", "consistency_available",
        "data_coverage", "warnings", "calculation_details", "created_at",
    }
    missing = required - cols
    assert not missing, f"Missing columns in company_technical_metrics: {missing}"


def test_technical_metrics_unique_constraint_exists():
    inspector = inspect(discovery_engine)
    constraints = inspector.get_unique_constraints("company_technical_metrics")
    names = {c["name"] for c in constraints}
    assert "uq_technical_run_company_horizon" in names


# ------------------------------------------------------------------ #
#  Schema inspection – group_scores                                    #
# ------------------------------------------------------------------ #

def test_group_scores_all_required_columns_exist():
    inspector = inspect(discovery_engine)
    cols = {c["name"] for c in inspector.get_columns("group_scores")}

    required = {
        "id", "run_id", "entity_type", "entity_name", "parent_sector", "parent_industry",
        "horizon", "technical_return_score", "technical_breadth_score",
        "technical_volume_score", "technical_consistency_score", "technical_score",
        "constituent_count", "eligible_constituent_count",
        "data_coverage", "warnings", "calculation_details",
    }
    missing = required - cols
    assert not missing, f"Missing columns in group_scores: {missing}"


def test_group_scores_unique_constraint_exists():
    inspector = inspect(discovery_engine)
    constraints = inspector.get_unique_constraints("group_scores")
    names = {c["name"] for c in constraints}
    assert "uq_group_score_hierarchy_horizon" in names
    assert "uq_group_score_run_type_name_horizon" not in names


# ------------------------------------------------------------------ #
#  Insert and read – company_technical_metrics                         #
# ------------------------------------------------------------------ #

def test_technical_metric_insert_and_read():
    session = _session()
    run_id = f"test_run_{uuid.uuid4().hex[:8]}"
    try:
        rec = _make_technical(run_id)
        session.add(rec)
        session.commit()

        fetched = session.query(CompanyTechnicalMetric).filter_by(run_id=run_id).first()
        assert fetched is not None
        assert fetched.as_of_date == datetime.date(2024, 3, 31)
        assert fetched.company_candle_date == "2024-03-31"
        assert fetched.benchmark_candle_date == "2024-03-31"
        assert fetched.current_close == 150.0
        assert fetched.start_close == 120.0
        assert fetched.benchmark_current_close == 21000.0
        assert fetched.benchmark_start_close == 19000.0
        assert fetched.company_consistency_score == 0.70
        assert fetched.return_available is True
        assert fetched.volume_available is True
        assert fetched.consistency_available is True
        assert fetched.warnings == []
    finally:
        _cleanup_technical(session, run_id)
        session.close()


def test_technical_metric_unique_constraint_enforced():
    session = _session()
    run_id = f"test_run_{uuid.uuid4().hex[:8]}"
    company_id = str(uuid.uuid4())
    try:
        r1 = _make_technical(run_id, company_id, "SHORT")
        r2 = _make_technical(run_id, company_id, "SHORT")  # same run+company+horizon
        session.add(r1)
        session.commit()

        session.add(r2)
        with pytest.raises(Exception):  # UniqueViolation
            session.commit()
        session.rollback()
    finally:
        _cleanup_technical(session, run_id)
        session.close()


def test_technical_metric_different_horizons_allowed():
    """Same run + company but different horizons should succeed."""
    session = _session()
    run_id = f"test_run_{uuid.uuid4().hex[:8]}"
    company_id = str(uuid.uuid4())
    try:
        for h in ("SHORT", "MID", "LONG"):
            session.add(_make_technical(run_id, company_id, h))
        session.commit()

        count = session.query(CompanyTechnicalMetric).filter_by(run_id=run_id).count()
        assert count == 3
    finally:
        _cleanup_technical(session, run_id)
        session.close()


# ------------------------------------------------------------------ #
#  Insert and read – group_scores                                      #
# ------------------------------------------------------------------ #

def test_group_score_insert_and_read():
    session = _session()
    run_id = f"test_run_{uuid.uuid4().hex[:8]}"
    try:
        rec = _make_group(run_id)
        session.add(rec)
        session.commit()

        fetched = session.query(GroupScore).filter_by(run_id=run_id).first()
        assert fetched is not None
        assert fetched.entity_type == "SECTOR"
        assert fetched.warnings == ["test_warning"]
        assert fetched.technical_return_score == 0.7
    finally:
        _cleanup_group(session, run_id)
        session.close()


def test_group_score_unique_constraint_enforced():
    session = _session()
    run_id = f"test_run_{uuid.uuid4().hex[:8]}"
    try:
        g1 = _make_group(run_id, "SECTOR", "Technology", "SHORT")
        g2 = _make_group(run_id, "SECTOR", "Technology", "SHORT")  # duplicate
        session.add(g1)
        session.commit()

        session.add(g2)
        with pytest.raises(Exception):  # UniqueViolation
            session.commit()
        session.rollback()
    finally:
        _cleanup_group(session, run_id)
        session.close()


def test_group_score_different_horizons_allowed():
    session = _session()
    run_id = f"test_run_{uuid.uuid4().hex[:8]}"
    try:
        for h in ("SHORT", "MID", "LONG"):
            session.add(_make_group(run_id, "SECTOR", "Technology", h))
        session.commit()
        count = session.query(GroupScore).filter_by(run_id=run_id).count()
        assert count == 3
    finally:
        _cleanup_group(session, run_id)
        session.close()


def test_group_score_different_entity_types_allowed():
    session = _session()
    run_id = f"test_run_{uuid.uuid4().hex[:8]}"
    try:
        session.add(_make_group(run_id, "SECTOR",        "Technology", "SHORT"))
        session.add(_make_group(run_id, "INDUSTRY",      "Technology", "SHORT"))
        session.add(_make_group(run_id, "BASIC_INDUSTRY","Technology", "SHORT"))
        session.commit()
        count = session.query(GroupScore).filter_by(run_id=run_id).count()
        assert count == 3
    finally:
        _cleanup_group(session, run_id)
        session.close()


# ------------------------------------------------------------------ #
#  Source DB isolation                                                 #
# ------------------------------------------------------------------ #

def test_source_db_not_modified():
    """Alembic must never touch the source database."""
    from database import source_engine
    inspector = inspect(source_engine)
    tables = inspector.get_table_names()
    assert "company_technical_metrics" not in tables
    assert "group_scores" not in tables

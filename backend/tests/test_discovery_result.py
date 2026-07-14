import datetime
import uuid

import pytest
from sqlalchemy import event, text

from database import DiscoverySessionLocal, discovery_engine, source_engine
from models.discovery import (
    DiscoveryRun,
    DiscoverySelection,
    GroupScore,
    StockCandidateSnapshot,
)
from services.discovery.discovery_result import (
    DiscoveryResultService,
    W_DUPLICATE_SELECTION,
    W_GROUP_SCORE_UNAVAILABLE,
    W_HIERARCHY_MISMATCH,
    W_STOCK_SNAPSHOT_UNAVAILABLE,
)


@pytest.fixture
def disc_session():
    session = DiscoverySessionLocal()
    _clean(session)
    yield session
    session.rollback()
    _clean(session)
    session.close()


def _clean(session):
    for table in [
        "discovery_selections",
        "stock_candidate_snapshots",
        "group_scores",
        "discovery_runs",
    ]:
        session.execute(text(f"DELETE FROM {table}"))
    session.commit()


def _run_id():
    return f"run_{uuid.uuid4().hex[:8]}"


def _make_run(session, run_id=None, status="COMPLETED", stage_results=None, warnings=None, error_code=None, error_message=None):
    run_id = run_id or _run_id()
    now = datetime.datetime(2026, 7, 13, 10, 0, 0)
    row = DiscoveryRun(
        id=run_id,
        run_date="2026-07-13",
        status=status,
        current_stage=None if status in {"COMPLETED", "FAILED"} else "SECTOR_RANKING",
        last_completed_stage="STOCK_RANKING" if status in {"COMPLETED", "COMPLETED_WITH_WARNINGS"} else "SECTOR_RANKING",
        started_at=now,
        completed_at=now + datetime.timedelta(minutes=5) if status in {"COMPLETED", "COMPLETED_WITH_WARNINGS", "FAILED"} else None,
        stage_results=stage_results if stage_results is not None else _completed_stage_results(),
        warnings=warnings or [],
        error_code=error_code,
        error_message=error_message,
        resume_count=0,
    )
    session.add(row)
    session.commit()
    return row


def _completed_stage_results():
    return {
        "STOCK_RANKING": {
            "status": "COMPLETED",
            "horizons": {
                horizon: {"status": "COMPLETED", "metadata": {}, "warnings": []}
                for horizon in ("SHORT", "MID", "LONG")
            },
        }
    }


def _stage_results_for(horizon_status):
    return {
        "STOCK_RANKING": {
            "status": horizon_status,
            "horizons": {
                "SHORT": {"status": horizon_status, "metadata": {}, "warnings": []},
                "MID": {"status": "PENDING", "metadata": {}, "warnings": []},
                "LONG": {"status": "PENDING", "metadata": {}, "warnings": []},
            },
        }
    }


def _make_group(session, run_id, horizon="SHORT", entity_type="SECTOR", name="Technology", parent_sector="", parent_industry="", rank=1):
    details_key = {
        "SECTOR": "final_sector_score",
        "INDUSTRY": "final_industry_score",
        "BASIC_INDUSTRY": "final_basic_industry_score",
    }[entity_type]
    row = GroupScore(
        id=str(uuid.uuid4()),
        run_id=run_id,
        horizon=horizon,
        entity_type=entity_type,
        entity_name=name,
        parent_sector=parent_sector,
        parent_industry=parent_industry,
        rank=rank,
        final_score=80.0 + rank,
        technical_score=81.0 + rank,
        fundamental_score=82.0 + rank,
        macro_score=83.0 + rank,
        data_coverage=99.0,
        warnings=["GROUP_WARN"],
        calculation_details={
            "discovery": {
                details_key: {
                    "status": "VERY_STRONG",
                    "coverage_pct": 97.5,
                    "score": 80.0 + rank,
                }
            }
        },
    )
    session.add(row)
    session.commit()
    return row


def _make_selection(session, run_id, horizon="SHORT", entity_type="SECTOR", name="Technology", parent_sector="", parent_industry="", basic_industry=None, company_id=None, symbol=None, rank=1):
    row = DiscoverySelection(
        id=str(uuid.uuid4()),
        run_id=run_id,
        horizon=horizon,
        entity_type=entity_type,
        entity_name=name,
        company_id=company_id,
        symbol=symbol,
        parent_sector=parent_sector,
        parent_industry=parent_industry,
        basic_industry=basic_industry,
        rank=rank,
        selected=True,
    )
    session.add(row)
    session.commit()
    return row


def _make_stock(session, run_id, horizon="SHORT", company_id="company-1", symbol="AAA", rank=1, selected=True):
    row = StockCandidateSnapshot(
        id=str(uuid.uuid4()),
        run_id=run_id,
        horizon=horizon,
        company_id=company_id,
        symbol=symbol,
        sector="Technology",
        industry="Software",
        basic_industry="Enterprise Software",
        technical_available=True,
        fundamental_available=True,
        eligible=True,
        status="ELIGIBLE",
        warnings=["UNIVERSE_WARN"],
        calculation_details={},
        technical_score=88.0,
        fundamental_score=80.0,
        inherited_macro_score=81.0,
        final_score=84.0,
        score_coverage_pct=100.0,
        score_status="VERY_STRONG",
        score_eligible=True,
        score_warnings=["SCORE_WARN"],
        score_details={},
        rank=rank,
        selected=selected,
    )
    session.add(row)
    session.commit()
    return row


def _make_full_short_result(session, run_id):
    _make_group(session, run_id, "SHORT", "SECTOR", "Technology", rank=1)
    _make_group(session, run_id, "SHORT", "INDUSTRY", "Software", "Technology", "", rank=1)
    _make_group(session, run_id, "SHORT", "BASIC_INDUSTRY", "Enterprise Software", "Technology", "Software", rank=1)
    _make_selection(session, run_id, "SHORT", "SECTOR", "Technology", rank=1)
    _make_selection(session, run_id, "SHORT", "INDUSTRY", "Software", "Technology", rank=1)
    _make_selection(session, run_id, "SHORT", "BASIC_INDUSTRY", "Enterprise Software", "Technology", "Software", rank=1)
    _make_selection(session, run_id, "SHORT", "STOCK", "AAA", "Technology", "Software", "Enterprise Software", "company-1", "AAA", 1)
    _make_selection(session, run_id, "SHORT", "STOCK", "BBB", "Technology", "Software", "Enterprise Software", "company-2", "BBB", 2)
    _make_stock(session, run_id, "SHORT", "company-1", "AAA", 1)
    _make_stock(session, run_id, "SHORT", "company-2", "BBB", 2)


def _result(session, run_id):
    return DiscoveryResultService(session).get_result(run_id)


def test_completed_run_result(disc_session):
    run = _make_run(disc_session)
    _make_full_short_result(disc_session, run.id)

    result = _result(disc_session, run.id)

    assert result["run_id"] == run.id
    assert result["status"] == "COMPLETED"
    assert result["error"] is None
    assert result["horizons"]["SHORT"]["sector"]["name"] == "Technology"


def test_all_three_horizons_are_always_returned(disc_session):
    run = _make_run(disc_session)

    result = _result(disc_session, run.id)

    assert list(result["horizons"]) == ["SHORT", "MID", "LONG"]


def test_sector_selection_mapping(disc_session):
    run = _make_run(disc_session)
    _make_group(disc_session, run.id, entity_type="SECTOR", name="Technology", rank=3)
    _make_selection(disc_session, run.id, entity_type="SECTOR", name="Technology", rank=3)

    sector = _result(disc_session, run.id)["horizons"]["SHORT"]["sector"]

    assert sector["name"] == "Technology"
    assert sector["rank"] == 3
    assert sector["final_score"] == 83.0


def test_industry_selection_mapping(disc_session):
    run = _make_run(disc_session)
    _make_group(disc_session, run.id, entity_type="SECTOR", name="Technology")
    _make_group(disc_session, run.id, entity_type="INDUSTRY", name="Software", parent_sector="Technology")
    _make_selection(disc_session, run.id, entity_type="SECTOR", name="Technology")
    _make_selection(disc_session, run.id, entity_type="INDUSTRY", name="Software", parent_sector="Technology")

    industry = _result(disc_session, run.id)["horizons"]["SHORT"]["industry"]

    assert industry["name"] == "Software"
    assert industry["parent_sector"] == "Technology"


def test_basic_industry_selection_mapping(disc_session):
    run = _make_run(disc_session)
    _make_full_short_result(disc_session, run.id)

    basic = _result(disc_session, run.id)["horizons"]["SHORT"]["basic_industry"]

    assert basic["name"] == "Enterprise Software"
    assert basic["parent_sector"] == "Technology"
    assert basic["parent_industry"] == "Software"


def test_multiple_selected_stocks_ordered_by_rank(disc_session):
    run = _make_run(disc_session)
    _make_full_short_result(disc_session, run.id)

    stocks = _result(disc_session, run.id)["horizons"]["SHORT"]["stocks"]

    assert [stock["symbol"] for stock in stocks] == ["AAA", "BBB"]


def test_full_hierarchy_validation(disc_session):
    run = _make_run(disc_session)
    _make_full_short_result(disc_session, run.id)

    horizon = _result(disc_session, run.id)["horizons"]["SHORT"]

    assert horizon["warnings"] == []
    assert len(horizon["stocks"]) == 2


def test_mismatched_industry_is_excluded(disc_session):
    run = _make_run(disc_session)
    _make_group(disc_session, run.id, entity_type="SECTOR", name="Technology")
    _make_group(disc_session, run.id, entity_type="INDUSTRY", name="Software", parent_sector="Finance")
    _make_selection(disc_session, run.id, entity_type="SECTOR", name="Technology")
    _make_selection(disc_session, run.id, entity_type="INDUSTRY", name="Software", parent_sector="Finance")

    horizon = _result(disc_session, run.id)["horizons"]["SHORT"]

    assert horizon["industry"] is None
    assert W_HIERARCHY_MISMATCH in horizon["warnings"]


def test_mismatched_basic_industry_is_excluded(disc_session):
    run = _make_run(disc_session)
    _make_full_short_result(disc_session, run.id)
    basic = disc_session.query(DiscoverySelection).filter_by(run_id=run.id, entity_type="BASIC_INDUSTRY").first()
    basic.parent_industry = "Hardware"
    disc_session.commit()

    horizon = _result(disc_session, run.id)["horizons"]["SHORT"]

    assert horizon["basic_industry"] is None
    assert W_HIERARCHY_MISMATCH in horizon["warnings"]


def test_mismatched_stock_is_excluded(disc_session):
    run = _make_run(disc_session)
    _make_full_short_result(disc_session, run.id)
    stock = disc_session.query(DiscoverySelection).filter_by(run_id=run.id, entity_type="STOCK", symbol="AAA").first()
    stock.basic_industry = "Wrong"
    disc_session.commit()

    stocks = _result(disc_session, run.id)["horizons"]["SHORT"]["stocks"]

    assert [stock["symbol"] for stock in stocks] == ["BBB"]


def test_missing_group_score_row_warning(disc_session):
    run = _make_run(disc_session)
    _make_selection(disc_session, run.id, entity_type="SECTOR", name="Technology")

    horizon = _result(disc_session, run.id)["horizons"]["SHORT"]

    assert horizon["sector"] is None
    assert W_GROUP_SCORE_UNAVAILABLE in horizon["warnings"]


def test_missing_stock_snapshot_warning(disc_session):
    run = _make_run(disc_session)
    _make_full_short_result(disc_session, run.id)
    disc_session.execute(text("DELETE FROM stock_candidate_snapshots WHERE company_id = 'company-1'"))
    disc_session.commit()

    horizon = _result(disc_session, run.id)["horizons"]["SHORT"]

    assert W_STOCK_SNAPSHOT_UNAVAILABLE in horizon["warnings"]
    assert [stock["symbol"] for stock in horizon["stocks"]] == ["BBB"]


def test_duplicate_active_selection_warning(disc_session):
    run = _make_run(disc_session)
    _make_group(disc_session, run.id, entity_type="SECTOR", name="Technology")
    _make_group(disc_session, run.id, entity_type="SECTOR", name="Finance", rank=2)
    _make_selection(disc_session, run.id, entity_type="SECTOR", name="Technology")
    _make_selection(disc_session, run.id, entity_type="SECTOR", name="Finance", rank=2)

    horizon = _result(disc_session, run.id)["horizons"]["SHORT"]

    assert horizon["sector"]["name"] == "Technology"
    assert W_DUPLICATE_SELECTION in horizon["warnings"]


def test_pending_run(disc_session):
    run = _make_run(disc_session, status="PENDING", stage_results={})

    result = _result(disc_session, run.id)

    assert result["status"] == "PENDING"
    assert result["horizons"]["SHORT"]["status"] == "PENDING"


def test_running_run_with_partial_results(disc_session):
    run = _make_run(disc_session, status="RUNNING", stage_results=_stage_results_for("RUNNING"))
    _make_group(disc_session, run.id, entity_type="SECTOR", name="Technology")
    _make_selection(disc_session, run.id, entity_type="SECTOR", name="Technology")

    horizon = _result(disc_session, run.id)["horizons"]["SHORT"]

    assert horizon["status"] == "RUNNING"
    assert horizon["sector"]["name"] == "Technology"
    assert horizon["industry"] is None


def test_failed_run_with_safe_error(disc_session):
    run = _make_run(
        disc_session,
        status="FAILED",
        error_code="SAFE_CODE",
        error_message="Authorization: Bearer secret-token\nTraceback raw",
        stage_results={},
    )

    error = _result(disc_session, run.id)["error"]

    assert error["code"] == "SAFE_CODE"
    assert "secret-token" not in error["message"]
    assert "Traceback" not in error["message"]


def test_completed_with_warnings_run(disc_session):
    run = _make_run(disc_session, status="COMPLETED_WITH_WARNINGS", warnings=["RUN_WARN"])

    result = _result(disc_session, run.id)

    assert result["status"] == "COMPLETED_WITH_WARNINGS"
    assert result["warnings"] == ["RUN_WARN"]


def test_skipped_horizon(disc_session):
    run = _make_run(disc_session, status="COMPLETED_WITH_WARNINGS", stage_results=_stage_results_for("SKIPPED"))

    assert _result(disc_session, run.id)["horizons"]["SHORT"]["status"] == "SKIPPED"


def test_status_and_coverage_are_read_without_recalculation(disc_session):
    run = _make_run(disc_session)
    _make_group(disc_session, run.id, entity_type="SECTOR", name="Technology")
    _make_selection(disc_session, run.id, entity_type="SECTOR", name="Technology")
    group = disc_session.query(GroupScore).filter_by(run_id=run.id, entity_type="SECTOR").first()
    group.final_score = 10.0
    details = dict(group.calculation_details)
    details["discovery"] = dict(details["discovery"])
    details["discovery"]["final_sector_score"] = dict(details["discovery"]["final_sector_score"])
    details["discovery"]["final_sector_score"]["status"] = "PERSISTED_STATUS"
    details["discovery"]["final_sector_score"]["coverage_pct"] = 12.5
    group.calculation_details = details
    disc_session.commit()

    sector = _result(disc_session, run.id)["horizons"]["SHORT"]["sector"]

    assert sector["status"] == "PERSISTED_STATUS"
    assert sector["coverage_pct"] == 12.5
    assert sector["final_score"] == 10.0


def test_persisted_ranks_are_not_recalculated(disc_session):
    run = _make_run(disc_session)
    _make_group(disc_session, run.id, entity_type="SECTOR", name="Technology", rank=9)
    _make_selection(disc_session, run.id, entity_type="SECTOR", name="Technology", rank=1)

    assert _result(disc_session, run.id)["horizons"]["SHORT"]["sector"]["rank"] == 9


def test_bulk_loading_avoids_per_stock_queries(disc_session):
    run = _make_run(disc_session)
    _make_full_short_result(disc_session, run.id)
    statements = []

    @event.listens_for(discovery_engine, "before_cursor_execute")
    def intercept(conn, cursor, statement, parameters, context, executemany):
        if "stock_candidate_snapshots" in statement.lower():
            statements.append(statement)

    try:
        _result(disc_session, run.id)
    finally:
        event.remove(discovery_engine, "before_cursor_execute", intercept)

    assert len([statement for statement in statements if statement.lstrip().upper().startswith("SELECT")]) == 1


def test_no_database_writes(disc_session):
    run = _make_run(disc_session)
    _make_full_short_result(disc_session, run.id)
    writes = []

    @event.listens_for(discovery_engine, "before_cursor_execute")
    def intercept(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            writes.append(statement)

    try:
        _result(disc_session, run.id)
    finally:
        event.remove(discovery_engine, "before_cursor_execute", intercept)

    assert writes == []


def test_no_pipeline_service_calls(disc_session):
    run = _make_run(disc_session)
    _make_full_short_result(disc_session, run.id)

    result = _result(disc_session, run.id)

    assert result["last_completed_stage"] == "STOCK_RANKING"


def test_no_parallel_or_llm_call(disc_session):
    run = _make_run(disc_session)
    service = DiscoveryResultService(disc_session)

    service.get_result(run.id)

    assert not hasattr(service, "_llm")
    assert not hasattr(service, "_provider")
    assert not hasattr(service, "_parallel")


def test_no_source_database_access(disc_session):
    accessed = []

    @event.listens_for(source_engine, "before_cursor_execute")
    def intercept(conn, cursor, statement, parameters, context, executemany):
        accessed.append(statement)

    try:
        run = _make_run(disc_session)
        _result(disc_session, run.id)
    finally:
        event.remove(source_engine, "before_cursor_execute", intercept)

    assert accessed == []


def test_secrets_and_raw_provider_responses_are_not_exposed(disc_session):
    run = _make_run(
        disc_session,
        stage_results={
            "MACRO_SEARCH": {
                "status": "FAILED",
                "error_message": "api_key=hidden\nTraceback raw",
                "raw_provider_response": {"token": "hidden"},
                "prompt": "hidden prompt",
            }
        },
    )

    stage = _result(disc_session, run.id)["stage_results"]["MACRO_SEARCH"]

    assert "raw_provider_response" not in stage
    assert "prompt" not in stage
    assert "hidden" not in stage["error_message"]

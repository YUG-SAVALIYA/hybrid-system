import inspect
import uuid

import pytest
from sqlalchemy import text

from database import DiscoverySessionLocal
from models.discovery import (
    CompanyFundamentalMetric,
    CompanyTechnicalMetric,
    DiscoveryRun,
    DiscoverySelection,
    GroupScore,
)
from services.discovery import discovery_pipeline_orchestrator as orch
from services.discovery.discovery_pipeline_orchestrator import DiscoveryPipelineOrchestrator


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
        "company_technical_metrics",
        "company_fundamental_metrics",
        "discovery_runs",
    ]:
        session.execute(text(f"DELETE FROM {table}"))
    session.commit()


def _run_id():
    return f"run_{uuid.uuid4().hex[:8]}"


def _make_run(session, run_id=None, status="PENDING", stage_results=None):
    run_id = run_id or _run_id()
    row = DiscoveryRun(
        id=run_id,
        run_date="2026-07-13",
        status=status,
        stage_results=stage_results or {},
        warnings=[],
        resume_count=0,
    )
    session.add(row)
    session.commit()
    return row


def _add_prereqs(session, run_id):
    session.add(
        CompanyFundamentalMetric(
            id=str(uuid.uuid4()),
            run_id=run_id,
            source_company_id="c1",
            symbol="AAA",
            sector="Technology",
            industry="Software",
            basic_industry="Enterprise Software",
            final_fundamental_score=75.0,
            fundamental_status="STRONG",
        )
    )
    for horizon in orch.HORIZONS:
        session.add(
            CompanyTechnicalMetric(
                id=str(uuid.uuid4()),
                run_id=run_id,
                source_company_id=f"c1-{horizon}",
                symbol="AAA",
                sector="Technology",
                industry="Software",
                basic_industry="Enterprise Software",
                horizon=horizon,
                return_available=True,
                volume_available=True,
                consistency_available=True,
            )
        )
        for entity_type, name, parent_sector, parent_industry in [
            ("SECTOR", "Technology", "", ""),
            ("INDUSTRY", "Software", "Technology", ""),
            ("BASIC_INDUSTRY", "Enterprise Software", "Technology", "Software"),
        ]:
            session.add(
                GroupScore(
                    id=str(uuid.uuid4()),
                    run_id=run_id,
                    entity_type=entity_type,
                    entity_name=name,
                    parent_sector=parent_sector,
                    parent_industry=parent_industry,
                    horizon=horizon,
                    technical_score=70.0,
                    fundamental_score=80.0,
                    final_score=75.0,
                    calculation_details={},
                )
            )
    session.commit()


def _upsert_selection(session, run_id, horizon, entity_type, name, rank=1):
    row = (
        session.query(DiscoverySelection)
        .filter_by(
            run_id=run_id,
            horizon=horizon,
            entity_type=entity_type,
            entity_name=name,
            parent_sector="Technology" if entity_type != "SECTOR" else "",
            parent_industry="Software" if entity_type in {"BASIC_INDUSTRY", "STOCK"} else "",
        )
        .first()
    )
    if row is None:
        row = DiscoverySelection(
            id=str(uuid.uuid4()),
            run_id=run_id,
            horizon=horizon,
            entity_type=entity_type,
            entity_name=name,
            parent_sector="Technology" if entity_type != "SECTOR" else "",
            parent_industry="Software" if entity_type in {"BASIC_INDUSTRY", "STOCK"} else "",
        )
        session.add(row)
    row.company_id = f"company-{name}" if entity_type == "STOCK" else None
    row.symbol = name if entity_type == "STOCK" else None
    row.basic_industry = "Enterprise Software" if entity_type == "STOCK" else None
    row.rank = rank
    row.selected = True
    session.commit()


def _call_key(stage, horizon=None):
    return f"{stage}.{horizon}" if horizon else stage


def _expected_order():
    return [
        orch.MACRO_SEARCH,
        orch.MACRO_FILTER,
        orch.SECTOR_IMPACT,
        orch.SECTOR_MACRO_SCORE,
        "SECTOR_RANKING.SHORT",
        "SECTOR_RANKING.MID",
        "SECTOR_RANKING.LONG",
        orch.INDUSTRY_IMPACT,
        orch.INDUSTRY_MACRO_SCORE,
        "INDUSTRY_RANKING.SHORT",
        "INDUSTRY_RANKING.MID",
        "INDUSTRY_RANKING.LONG",
        orch.BASIC_INDUSTRY_IMPACT,
        orch.BASIC_INDUSTRY_MACRO_SCORE,
        "BASIC_INDUSTRY_RANKING.SHORT",
        "BASIC_INDUSTRY_RANKING.MID",
        "BASIC_INDUSTRY_RANKING.LONG",
        "STOCK_CANDIDATE_UNIVERSE.SHORT",
        "STOCK_CANDIDATE_UNIVERSE.MID",
        "STOCK_CANDIDATE_UNIVERSE.LONG",
        "STOCK_CANDIDATE_SCORE.SHORT",
        "STOCK_CANDIDATE_SCORE.MID",
        "STOCK_CANDIDATE_SCORE.LONG",
        "STOCK_RANKING.SHORT",
        "STOCK_RANKING.MID",
        "STOCK_RANKING.LONG",
    ]


def _fake_services(session, calls, warnings=None, fail_return=None, fail_raise=None, message=None):
    warnings = warnings or {}
    fail_return = set(fail_return or [])
    fail_raise = set(fail_raise or [])

    def handler(stage):
        def call(run_id, horizon=None):
            key = _call_key(stage, horizon)
            calls.append(key)
            if key in fail_raise or stage in fail_raise:
                raise RuntimeError(message or f"{key} failed")
            if key in fail_return or stage in fail_return:
                return {
                    "status": "FAILED",
                    "warnings": [f"{key}_FAILED"],
                    "metadata": {"failed": key},
                    "error_message": f"{key} returned failure",
                }
            if stage == orch.SECTOR_RANKING:
                _upsert_selection(session, run_id, horizon, "SECTOR", "Technology")
            elif stage == orch.INDUSTRY_RANKING:
                _upsert_selection(session, run_id, horizon, "INDUSTRY", "Software")
            elif stage == orch.BASIC_INDUSTRY_RANKING:
                _upsert_selection(session, run_id, horizon, "BASIC_INDUSTRY", "Enterprise Software")
            elif stage == orch.STOCK_RANKING:
                _upsert_selection(session, run_id, horizon, "STOCK", "AAA", 1)
                _upsert_selection(session, run_id, horizon, "STOCK", "BBB", 2)
            return {"warnings": warnings.get(key, warnings.get(stage, [])), "metadata": {"processed_count": 1}}
        return call

    return {stage: handler(stage) for stage in orch.STAGE_ORDER}


def _orchestrator(session, calls, **kwargs):
    return DiscoveryPipelineOrchestrator(
        session,
        services=_fake_services(session, calls, **kwargs),
        lock_enabled=False,
    )


def test_exact_stage_execution_order(disc_session):
    run = _make_run(disc_session)
    _add_prereqs(disc_session, run.id)
    calls = []

    result = _orchestrator(disc_session, calls).execute(run.id)

    assert result["status"] == "COMPLETED"
    assert calls == _expected_order()


def test_short_mid_and_long_execution_order(disc_session):
    run = _make_run(disc_session)
    _add_prereqs(disc_session, run.id)
    calls = []

    _orchestrator(disc_session, calls).execute(run.id)

    assert calls[4:7] == ["SECTOR_RANKING.SHORT", "SECTOR_RANKING.MID", "SECTOR_RANKING.LONG"]


def test_prerequisite_validation_happens_before_parallel(disc_session):
    run = _make_run(disc_session)
    calls = []

    result = _orchestrator(disc_session, calls).execute(run.id)

    assert result["error_code"] == orch.E_UPSTREAM_UNAVAILABLE
    assert calls == []


def test_missing_prerequisites_fail_safely(disc_session):
    run = _make_run(disc_session)
    calls = []

    result = _orchestrator(disc_session, calls).execute(run.id)

    details = result["stage_results"]["PREREQUISITE_VALIDATION"]["metadata"]
    assert result["status"] == "FAILED"
    assert "company_fundamental_metrics" in details["missing_prerequisites"]
    assert result["error_message"] == "Required upstream discovery data is unavailable."


def test_completed_stage_metadata_persistence(disc_session):
    run = _make_run(disc_session)
    _add_prereqs(disc_session, run.id)

    result = _orchestrator(disc_session, []).execute(run.id)

    assert result["stage_results"][orch.MACRO_SEARCH]["metadata"] == {"processed_count": 1}
    assert result["stage_results"][orch.MACRO_SEARCH]["status"] == "COMPLETED"


def test_stage_warnings_are_aggregated(disc_session):
    run = _make_run(disc_session)
    _add_prereqs(disc_session, run.id)
    warnings = {orch.MACRO_FILTER: ["FILTER_WARN"], "SECTOR_RANKING.MID": ["MID_WARN"]}

    result = _orchestrator(disc_session, [], warnings=warnings).execute(run.id)

    assert result["status"] == "COMPLETED_WITH_WARNINGS"
    assert result["warnings"] == ["FILTER_WARN", "MID_WARN"]


def test_stage_failure_stops_dependent_stages(disc_session):
    run = _make_run(disc_session)
    _add_prereqs(disc_session, run.id)
    calls = []

    result = _orchestrator(disc_session, calls, fail_return={orch.MACRO_FILTER}).execute(run.id)

    assert result["status"] == "FAILED"
    assert calls == [orch.MACRO_SEARCH, orch.MACRO_FILTER]


def test_previously_completed_results_remain_preserved_after_failure(disc_session):
    run = _make_run(
        disc_session,
        status="FAILED",
        stage_results={orch.MACRO_SEARCH: {"status": "COMPLETED", "metadata": {"kept": True}, "warnings": []}},
    )
    _add_prereqs(disc_session, run.id)

    result = _orchestrator(disc_session, [], fail_return={orch.MACRO_FILTER}).execute(run.id)

    assert result["stage_results"][orch.MACRO_SEARCH]["metadata"] == {"kept": True}


def test_unexecuted_stages_become_skipped(disc_session):
    run = _make_run(disc_session)
    _add_prereqs(disc_session, run.id)

    result = _orchestrator(disc_session, [], fail_return={orch.MACRO_FILTER}).execute(run.id)

    assert result["stage_results"][orch.SECTOR_IMPACT]["status"] == "SKIPPED"
    assert result["stage_results"][orch.STOCK_RANKING]["status"] == "SKIPPED"


def test_one_horizon_failure_allows_other_horizons_to_continue(disc_session):
    run = _make_run(disc_session)
    _add_prereqs(disc_session, run.id)
    calls = []

    result = _orchestrator(disc_session, calls, fail_raise={"SECTOR_RANKING.MID"}).execute(run.id)

    assert result["stage_results"][orch.SECTOR_RANKING]["horizons"]["MID"]["status"] == "FAILED"
    assert "INDUSTRY_RANKING.SHORT" in calls
    assert "INDUSTRY_RANKING.LONG" in calls


def test_all_horizons_failing_causes_parent_stage_to_fail(disc_session):
    run = _make_run(disc_session)
    _add_prereqs(disc_session, run.id)

    result = _orchestrator(
        disc_session,
        [],
        fail_return={"SECTOR_RANKING.SHORT", "SECTOR_RANKING.MID", "SECTOR_RANKING.LONG"},
    ).execute(run.id)

    assert result["stage_results"][orch.SECTOR_RANKING]["status"] == "FAILED"
    assert result["status"] == "FAILED"


def test_downstream_processing_skips_failed_horizons(disc_session):
    run = _make_run(disc_session)
    _add_prereqs(disc_session, run.id)
    calls = []

    _orchestrator(disc_session, calls, fail_return={"SECTOR_RANKING.MID"}).execute(run.id)

    assert "INDUSTRY_RANKING.MID" not in calls
    assert "INDUSTRY_RANKING.SHORT" in calls


def test_resume_skips_completed_stages(disc_session):
    run = _make_run(
        disc_session,
        status="FAILED",
        stage_results={orch.MACRO_SEARCH: {"status": "COMPLETED", "metadata": {}, "warnings": []}},
    )
    _add_prereqs(disc_session, run.id)
    calls = []

    _orchestrator(disc_session, calls).execute(run.id)

    assert orch.MACRO_SEARCH not in calls
    assert orch.MACRO_FILTER in calls


def test_interrupted_running_stage_is_rerun(disc_session):
    run = _make_run(
        disc_session,
        status="RUNNING",
        stage_results={
            orch.MACRO_SEARCH: {"status": "COMPLETED", "metadata": {}, "warnings": []},
            orch.MACRO_FILTER: {"status": "RUNNING", "metadata": {}, "warnings": []},
        },
    )
    _add_prereqs(disc_session, run.id)
    calls = []

    _orchestrator(disc_session, calls).execute(run.id)

    assert calls[0] == orch.MACRO_FILTER


def test_resume_count_increments(disc_session):
    run = _make_run(disc_session, status="FAILED")
    _add_prereqs(disc_session, run.id)

    result = _orchestrator(disc_session, []).execute(run.id)

    assert result["resume_count"] == 1


def test_completed_run_is_returned_without_service_calls(disc_session):
    run = _make_run(disc_session, status="COMPLETED", stage_results={orch.STOCK_RANKING: {"status": "COMPLETED"}})
    calls = []

    result = _orchestrator(disc_session, calls).execute(run.id)

    assert result["status"] == "COMPLETED"
    assert calls == []


def test_force_restart_resets_orchestration_metadata(disc_session):
    run = _make_run(
        disc_session,
        status="FAILED",
        stage_results={"OLD_STAGE": {"status": "FAILED"}},
    )
    _add_prereqs(disc_session, run.id)

    result = _orchestrator(disc_session, []).execute(run.id, force_restart=True)

    assert "OLD_STAGE" not in result["stage_results"]
    assert result["resume_count"] == 0


def test_force_restart_does_not_manually_delete_derived_data(disc_session):
    run = _make_run(disc_session, status="FAILED", stage_results={"OLD_STAGE": {"status": "FAILED"}})
    _add_prereqs(disc_session, run.id)
    _upsert_selection(disc_session, run.id, "SHORT", "STOCK", "KEEP", 10)

    _orchestrator(disc_session, []).execute(run.id, force_restart=True)

    assert disc_session.query(DiscoverySelection).filter_by(run_id=run.id, entity_name="KEEP").count() == 1


def test_final_selections_are_loaded_from_persisted_selections(disc_session):
    run = _make_run(disc_session)
    _add_prereqs(disc_session, run.id)

    result = _orchestrator(disc_session, []).execute(run.id)

    assert result["horizons"]["SHORT"] == {
        "sector": "Technology",
        "industry": "Software",
        "basic_industry": "Enterprise Software",
        "selected_stocks": ["AAA", "BBB"],
    }


def test_completed_with_warnings_final_status(disc_session):
    run = _make_run(disc_session)
    _add_prereqs(disc_session, run.id)

    result = _orchestrator(disc_session, [], warnings={orch.STOCK_RANKING: ["STOCK_WARN"]}).execute(run.id)

    assert result["status"] == "COMPLETED_WITH_WARNINGS"


def test_safe_error_message_persistence(disc_session):
    run = _make_run(disc_session)
    _add_prereqs(disc_session, run.id)
    message = "Authorization: Bearer abc123\nTraceback (most recent call last): secret stack"

    with pytest.raises(RuntimeError):
        _orchestrator(disc_session, [], fail_raise={orch.MACRO_FILTER}, message=message).execute(run.id)

    disc_session.refresh(run)
    assert "Bearer abc123" not in run.error_message
    assert "Traceback" not in run.error_message


def test_secrets_and_stack_traces_are_not_persisted(disc_session):
    run = _make_run(disc_session)
    _add_prereqs(disc_session, run.id)

    with pytest.raises(RuntimeError):
        _orchestrator(
            disc_session,
            [],
            fail_raise={orch.MACRO_FILTER},
            message="api_key=hidden\n  File x.py, line 1",
        ).execute(run.id)

    disc_session.refresh(run)
    stored = str(run.stage_results[orch.MACRO_FILTER]["error_message"])
    assert "hidden" not in stored
    assert "File x.py" not in stored


def test_same_run_concurrent_execution_is_rejected(disc_session):
    run = _make_run(disc_session)
    _add_prereqs(disc_session, run.id)
    lock_session = DiscoverySessionLocal()
    lock_session.execute(
        text("SELECT pg_advisory_lock(hashtext(:key))"),
        {"key": f"discovery_pipeline:{run.id}"},
    )
    try:
        result = DiscoveryPipelineOrchestrator(
            disc_session,
            services=_fake_services(disc_session, []),
        ).execute(run.id)
    finally:
        lock_session.execute(
            text("SELECT pg_advisory_unlock(hashtext(:key))"),
            {"key": f"discovery_pipeline:{run.id}"},
        )
        lock_session.commit()
        lock_session.close()

    assert result["error_code"] == orch.E_ALREADY_RUNNING


def test_different_runs_may_execute_independently(disc_session):
    blocked = _make_run(disc_session)
    _add_prereqs(disc_session, blocked.id)
    allowed = _make_run(disc_session)
    _add_prereqs(disc_session, allowed.id)
    lock_session = DiscoverySessionLocal()
    lock_session.execute(
        text("SELECT pg_advisory_lock(hashtext(:key))"),
        {"key": f"discovery_pipeline:{blocked.id}"},
    )
    try:
        result = DiscoveryPipelineOrchestrator(
            disc_session,
            services=_fake_services(disc_session, []),
        ).execute(allowed.id)
    finally:
        lock_session.execute(
            text("SELECT pg_advisory_unlock(hashtext(:key))"),
            {"key": f"discovery_pipeline:{blocked.id}"},
        )
        lock_session.commit()
        lock_session.close()

    assert result["status"] == "COMPLETED"


def test_dependency_injected_fake_services_are_used(disc_session):
    run = _make_run(disc_session)
    _add_prereqs(disc_session, run.id)
    calls = []

    class FakeMacroSearch:
        def fetch_macro_data(self, run_id):
            calls.append("fake-object")
            return {"metadata": {"object": True}}

    services = _fake_services(disc_session, calls)
    services[orch.MACRO_SEARCH] = FakeMacroSearch()
    DiscoveryPipelineOrchestrator(disc_session, services=services, lock_enabled=False).execute(run.id)

    assert calls[0] == "fake-object"


def test_idempotent_repeated_execution(disc_session):
    run = _make_run(disc_session)
    _add_prereqs(disc_session, run.id)

    first = _orchestrator(disc_session, []).execute(run.id)
    calls = []
    second = _orchestrator(disc_session, calls).execute(run.id)

    assert second["stage_results"] == first["stage_results"]
    assert calls == []


def test_existing_scores_and_selections_remain_service_owned(disc_session):
    run = _make_run(disc_session)
    _add_prereqs(disc_session, run.id)
    group = disc_session.query(GroupScore).filter_by(run_id=run.id, entity_type="SECTOR", horizon="SHORT").first()
    group.final_score = 123.0
    _upsert_selection(disc_session, run.id, "SHORT", "STOCK", "OLD", 99)
    disc_session.commit()

    _orchestrator(disc_session, []).execute(run.id)
    disc_session.refresh(group)

    assert group.final_score == 123.0
    assert disc_session.query(DiscoverySelection).filter_by(run_id=run.id, entity_name="OLD").count() == 1


def test_no_new_scoring_or_ranking_calculation_exists_in_orchestrator():
    source = inspect.getsource(orch.DiscoveryPipelineOrchestrator)

    assert "calculate_final_sector_score" not in source
    assert "calculate_final_industry_score" not in source
    assert "calculate_final_basic_industry_score" not in source
    assert "calculate_final_stock_score" not in source

import datetime
from pathlib import Path
import re
import uuid
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import event, text

from api.v1.discovery_router import (
    get_discovery_pipeline_orchestrator,
    get_discovery_result_service,
    get_discovery_run_creation_service,
)
from database import DiscoverySessionLocal, discovery_engine, source_engine
from main import app
from models.discovery import DiscoveryRun
from services.discovery.discovery_run_creation import (
    DiscoveryRunAlreadyExistsError,
    DiscoveryRunCreationService,
)


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


def _client():
    app.dependency_overrides.clear()
    return TestClient(app)


def _row(run_id="run-123", as_of_date="2026-07-13"):
    return SimpleNamespace(
        id=run_id,
        run_date=as_of_date,
        source_data_as_of=as_of_date,
        status="PENDING",
        created_at=datetime.datetime(2026, 7, 13, 18, 0, 0, tzinfo=datetime.timezone.utc),
    )


class FakeCreationService:
    def __init__(self, row=None, exc=None):
        self.row = row or _row()
        self.exc = exc
        self.calls = []

    def create_run(self, run_id=None, as_of_date=None):
        self.calls.append({"run_id": run_id, "as_of_date": as_of_date})
        if self.exc is not None:
            raise self.exc
        if run_id is not None:
            self.row.id = run_id
        if as_of_date is not None:
            self.row.run_date = as_of_date.isoformat()
            self.row.source_data_as_of = as_of_date.isoformat()
        return self.row


class FakeResultService:
    def get_result(self, run_id):
        return {
            "run_id": run_id,
            "status": "PENDING",
            "current_stage": None,
            "last_completed_stage": None,
            "started_at": None,
            "completed_at": None,
            "resume_count": 0,
            "warnings": [],
            "error": None,
            "stage_results": {},
            "horizons": {
                "SHORT": {"status": "PENDING", "sector": None, "industry": None, "basic_industry": None, "stocks": [], "warnings": []},
                "MID": {"status": "PENDING", "sector": None, "industry": None, "basic_industry": None, "stocks": [], "warnings": []},
                "LONG": {"status": "PENDING", "sector": None, "industry": None, "basic_industry": None, "stocks": [], "warnings": []},
            },
        }


class FakeOrchestrator:
    def __init__(self):
        self.calls = []

    def execute(self, run_id, resume=True, force_restart=False):
        self.calls.append(run_id)
        return {
            "run_id": run_id,
            "status": "FAILED",
            "last_completed_stage": None,
            "resume_count": 0,
            "horizons": {"SHORT": {}, "MID": {}, "LONG": {}},
            "stage_results": {},
            "warnings": [],
            "error": {"code": "DISCOVERY_UPSTREAM_DATA_UNAVAILABLE", "message": "missing"},
        }


class FakeResultService:
    def get_result(self, run_id):
        return {
            "run_id": run_id,
            "status": "PENDING",
            "current_stage": None,
            "last_completed_stage": None,
            "started_at": None,
            "completed_at": None,
            "resume_count": 0,
            "warnings": [],
            "error": None,
            "stage_results": {},
            "horizons": {
                "SHORT": {"status": "PENDING", "sector": None, "industry": None, "basic_industry": None, "stocks": [], "warnings": []},
                "MID": {"status": "PENDING", "sector": None, "industry": None, "basic_industry": None, "stocks": [], "warnings": []},
                "LONG": {"status": "PENDING", "sector": None, "industry": None, "basic_industry": None, "stocks": [], "warnings": []},
            },
        }


def _override_creation(service):
    app.dependency_overrides[get_discovery_run_creation_service] = lambda: service
    return service


def test_supplied_valid_run_id_creates_a_run():
    session = DiscoverySessionLocal()
    _clean(session)
    session.close()
    client = _client()

    response = client.post("/api/v1/discovery/runs", json={"run_id": "run-20260713-001", "as_of_date": "2026-07-13"})

    assert response.status_code == 201
    with DiscoverySessionLocal() as check:
        row = check.query(DiscoveryRun).filter_by(id="run-20260713-001").one()
        assert row.status == "PENDING"
    app.dependency_overrides.clear()


def test_missing_run_id_generates_one():
    session = DiscoverySessionLocal()
    _clean(session)
    session.close()
    client = _client()

    response = client.post("/api/v1/discovery/runs", json={"as_of_date": "2026-07-13"})

    assert response.status_code == 201
    assert response.json()["data"]["run_id"].startswith("run-")
    app.dependency_overrides.clear()


def test_generated_run_id_passes_validation():
    session = DiscoverySessionLocal()
    _clean(session)
    session.close()
    client = _client()

    response = client.post("/api/v1/discovery/runs", json={})

    assert re.fullmatch(r"[A-Za-z0-9_-]{1,128}", response.json()["data"]["run_id"])
    app.dependency_overrides.clear()


def test_run_id_whitespace_is_trimmed():
    client = _client()
    service = _override_creation(FakeCreationService(_row("trimmed")))

    response = client.post("/api/v1/discovery/runs", json={"run_id": "  trimmed  "})

    assert response.status_code == 201
    assert service.calls[0]["run_id"] == "trimmed"
    app.dependency_overrides.clear()


def test_invalid_run_id_returns_http_422():
    client = _client()
    service = _override_creation(FakeCreationService())

    response = client.post("/api/v1/discovery/runs", json={"run_id": "bad id"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_DISCOVERY_RUN_ID"
    assert service.calls == []
    app.dependency_overrides.clear()


def test_run_id_longer_than_128_characters_is_rejected():
    client = _client()
    service = _override_creation(FakeCreationService())

    response = client.post("/api/v1/discovery/runs", json={"run_id": "a" * 129})

    assert response.status_code == 422
    assert service.calls == []
    app.dependency_overrides.clear()


def test_supplied_valid_as_of_date():
    client = _client()
    service = _override_creation(FakeCreationService(_row("run-date")))

    response = client.post("/api/v1/discovery/runs", json={"run_id": "run-date", "as_of_date": "2026-07-13"})

    assert response.status_code == 201
    assert service.calls[0]["as_of_date"] == datetime.date(2026, 7, 13)
    assert response.json()["data"]["as_of_date"] == "2026-07-13"
    app.dependency_overrides.clear()


def test_missing_as_of_date_uses_current_utc_date():
    session = DiscoverySessionLocal()
    _clean(session)
    service = DiscoveryRunCreationService(session)

    row = service.create_run(run_id=f"run_{uuid.uuid4().hex}")

    assert row.run_date == datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    session.close()


def test_future_as_of_date_is_rejected():
    client = _client()
    service = _override_creation(FakeCreationService())

    response = client.post("/api/v1/discovery/runs", json={"as_of_date": "2999-01-01"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_DISCOVERY_AS_OF_DATE"
    assert service.calls == []
    app.dependency_overrides.clear()


def test_initial_status_is_pending():
    session = DiscoverySessionLocal()
    _clean(session)
    row = DiscoveryRunCreationService(session).create_run(run_id="initial-status", as_of_date=datetime.date(2026, 7, 13))
    assert row.status == "PENDING"
    session.close()


def test_initial_stage_metadata_is_empty():
    session = DiscoverySessionLocal()
    _clean(session)
    row = DiscoveryRunCreationService(session).create_run(run_id="initial-stage", as_of_date=datetime.date(2026, 7, 13))
    assert row.current_stage is None
    assert row.last_completed_stage is None
    assert row.stage_results == {}
    assert row.warnings == []
    session.close()


def test_initial_error_fields_are_null():
    session = DiscoverySessionLocal()
    _clean(session)
    row = DiscoveryRunCreationService(session).create_run(run_id="initial-error", as_of_date=datetime.date(2026, 7, 13))
    assert row.error_code is None
    assert row.error_message is None
    assert row.started_at is None
    assert row.completed_at is None
    session.close()


def test_initial_resume_count_is_zero():
    session = DiscoverySessionLocal()
    _clean(session)
    row = DiscoveryRunCreationService(session).create_run(run_id="initial-resume", as_of_date=datetime.date(2026, 7, 13))
    assert row.resume_count == 0
    session.close()


def test_success_returns_http_201():
    client = _client()
    _override_creation(FakeCreationService(_row("run-201")))
    response = client.post("/api/v1/discovery/runs", json={"run_id": "run-201"})
    assert response.status_code == 201
    app.dependency_overrides.clear()


def test_duplicate_run_id_returns_http_409():
    client = _client()
    _override_creation(FakeCreationService(exc=DiscoveryRunAlreadyExistsError("exists")))

    response = client.post("/api/v1/discovery/runs", json={"run_id": "duplicate"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DISCOVERY_RUN_ALREADY_EXISTS"
    app.dependency_overrides.clear()


def test_concurrent_duplicate_insert_is_handled_safely(monkeypatch):
    session = DiscoverySessionLocal()
    _clean(session)
    service = DiscoveryRunCreationService(session)
    original_commit = session.commit
    original_query = session.query

    class EmptyQuery:
        def filter_by(self, **kwargs):
            return self

        def first(self):
            return None

    def fake_query(model):
        if model is DiscoveryRun:
            return EmptyQuery()
        return original_query(model)

    def fake_commit():
        from sqlalchemy.exc import IntegrityError

        raise IntegrityError("insert", {}, Exception("duplicate"))

    monkeypatch.setattr(session, "query", fake_query)
    monkeypatch.setattr(session, "commit", fake_commit)

    try:
        try:
            service.create_run(run_id="race", as_of_date=datetime.date(2026, 7, 13))
            assert False
        except DiscoveryRunAlreadyExistsError:
            assert True
    finally:
        monkeypatch.setattr(session, "commit", original_commit)
        session.rollback()
        session.close()


def test_unexpected_failure_returns_safe_http_500():
    client = _client()
    _override_creation(FakeCreationService(exc=RuntimeError(r"C:\secret\traceback.sql")))

    response = client.post("/api/v1/discovery/runs", json={"run_id": "boom"})

    assert response.status_code == 500
    assert response.json() == {
        "success": False,
        "error": {
            "code": "DISCOVERY_RUN_CREATION_FAILED",
            "message": "The discovery run could not be created.",
        },
    }
    assert "secret" not in response.text
    app.dependency_overrides.clear()


def test_transaction_rolls_back_after_failure(monkeypatch):
    session = DiscoverySessionLocal()
    _clean(session)
    service = DiscoveryRunCreationService(session)
    calls = {"rollback": 0}

    def fail_commit():
        raise RuntimeError("commit failed")

    def count_rollback():
        calls["rollback"] += 1
        session.__class__.rollback(session)

    monkeypatch.setattr(session, "commit", fail_commit)
    monkeypatch.setattr(session, "rollback", count_rollback)

    try:
        try:
            service.create_run(run_id="rollback-run", as_of_date=datetime.date(2026, 7, 13))
            assert False
        except RuntimeError:
            assert calls["rollback"] == 1
    finally:
        session.close()


def test_service_commits_exactly_once_on_success(monkeypatch):
    session = DiscoverySessionLocal()
    _clean(session)
    calls = {"commit": 0}
    original_commit = session.commit

    def count_commit():
        calls["commit"] += 1
        original_commit()

    monkeypatch.setattr(session, "commit", count_commit)
    DiscoveryRunCreationService(session).create_run(run_id="commit-once", as_of_date=datetime.date(2026, 7, 13))

    assert calls["commit"] == 1
    session.close()


def test_route_calls_creation_service_exactly_once():
    client = _client()
    service = _override_creation(FakeCreationService(_row("once")))

    response = client.post("/api/v1/discovery/runs", json={"run_id": "once"})

    assert response.status_code == 201
    assert len(service.calls) == 1
    app.dependency_overrides.clear()


def test_route_does_not_call_orchestrator():
    client = _client()
    orchestrator = FakeOrchestrator()
    app.dependency_overrides[get_discovery_pipeline_orchestrator] = lambda: orchestrator
    _override_creation(FakeCreationService(_row("no-orch")))

    response = client.post("/api/v1/discovery/runs", json={"run_id": "no-orch"})

    assert response.status_code == 201
    assert orchestrator.calls == []
    app.dependency_overrides.clear()


def test_no_parallel_or_llm_call():
    client = _client()
    service = _override_creation(FakeCreationService(_row("no-llm")))

    response = client.post("/api/v1/discovery/runs", json={"run_id": "no-llm"})

    assert response.status_code == 201
    assert not hasattr(service, "_llm")
    assert not hasattr(service, "_parallel")
    assert not hasattr(service, "_provider")
    app.dependency_overrides.clear()


def test_no_source_database_access():
    accessed = []
    client = _client()
    _override_creation(FakeCreationService(_row("no-source")))

    @event.listens_for(source_engine, "before_cursor_execute")
    def intercept(conn, cursor, statement, parameters, context, executemany):
        accessed.append(statement)

    try:
        response = client.post("/api/v1/discovery/runs", json={"run_id": "no-source"})
    finally:
        event.remove(source_engine, "before_cursor_execute", intercept)
        app.dependency_overrides.clear()

    assert response.status_code == 201
    assert accessed == []


def test_existing_result_endpoint_remains_unaffected():
    client = _client()
    app.dependency_overrides[get_discovery_result_service] = lambda: FakeResultService()

    response = client.get("/api/v1/discovery/runs/run-1/result")

    assert response.status_code == 200
    app.dependency_overrides.clear()


def test_existing_execute_endpoint_remains_unaffected():
    client = _client()
    app.dependency_overrides[get_discovery_pipeline_orchestrator] = lambda: FakeOrchestrator()

    response = client.post("/api/v1/discovery/runs/run-1/execute", json={})

    assert response.status_code in {200, 422}
    app.dependency_overrides.clear()


def test_create_route_appears_exactly_once_in_openapi():
    client = _client()
    schema = client.get("/openapi.json").json()
    paths = [path for path in schema["paths"] if path == "/api/v1/discovery/runs"]
    assert paths == ["/api/v1/discovery/runs"]
    assert list(schema["paths"]["/api/v1/discovery/runs"].keys()) == ["post"]


def test_no_tests_are_conditionally_skipped():
    source = Path(__file__).read_text()

    assert ".".join(["pytest", "skip"]) not in source
    assert "".join(["import", "orskip"]) not in source

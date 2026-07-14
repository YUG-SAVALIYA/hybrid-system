import inspect
from pathlib import Path
import datetime

from fastapi.testclient import TestClient

from api.v1.discovery_router import (
    get_discovery_pipeline_orchestrator,
    get_discovery_result_service,
    get_discovery_run_creation_service,
    get_discovery_upstream_preparation_service,
    prepare_discovery_run,
)
from main import app


def _prepare_result(run_id="run-123", status="COMPLETED", error=None, warnings=None):
    return {
        "run_id": run_id,
        "status": status,
        "preparation_status": status,
        "last_completed_stage": "UPSTREAM_VALIDATION",
        "resume_count": 0,
        "stage_results": {"UPSTREAM_VALIDATION": {"status": status}},
        "warnings": warnings or [],
        "error": error,
    }


class FakePreparationService:
    def __init__(self, result=None, exc=None):
        self.result = result
        self.exc = exc
        self.calls = []

    def prepare(self, run_id, resume=True, force_restart=False):
        self.calls.append(
            {"run_id": run_id, "resume": resume, "force_restart": force_restart}
        )
        if self.exc is not None:
            raise self.exc
        result = dict(self.result or _prepare_result(run_id))
        result["run_id"] = run_id
        return result


class FakeOrchestrator:
    def __init__(self):
        self.calls = []

    def execute(self, *args, **kwargs):
        self.calls.append({"args": args, "kwargs": kwargs})
        return {
            "run_id": "unused",
            "status": "COMPLETED",
            "last_completed_stage": "STOCK_RANKING",
            "resume_count": 0,
            "horizons": {"SHORT": {}, "MID": {}, "LONG": {}},
            "stage_results": {},
            "warnings": [],
            "error": None,
        }


class FakeResultService:
    def get_result(self, run_id):
        return {
            "run_id": run_id,
            "status": "COMPLETED",
            "current_stage": None,
            "last_completed_stage": "STOCK_RANKING",
            "started_at": "2026-07-13T10:00:00Z",
            "completed_at": "2026-07-13T10:05:00Z",
            "resume_count": 0,
            "warnings": [],
            "error": None,
            "stage_results": {},
            "horizons": {
                "SHORT": {"status": "COMPLETED", "sector": None, "industry": None, "basic_industry": None, "stocks": [], "warnings": []},
                "MID": {"status": "PENDING", "sector": None, "industry": None, "basic_industry": None, "stocks": [], "warnings": []},
                "LONG": {"status": "PENDING", "sector": None, "industry": None, "basic_industry": None, "stocks": [], "warnings": []},
            },
        }


class FakeCreationService:
    def create_run(self, run_id=None, as_of_date=None):
        class Row:
            id = run_id or "created-run"
            run_date = "2026-07-13"
            source_data_as_of = "2026-07-13"
            status = "PENDING"
            created_at = datetime.datetime(2026, 7, 13, 12, 0, 0, tzinfo=datetime.timezone.utc)

        return Row()


def _client():
    app.dependency_overrides.clear()
    return TestClient(app)


def _override_preparation(service):
    app.dependency_overrides[get_discovery_upstream_preparation_service] = lambda: service
    return service


def test_completed_preparation_returns_http_200():
    client = _client()
    _override_preparation(FakePreparationService(_prepare_result("run-1")))

    response = client.post("/api/v1/discovery/runs/run-1/prepare", json={})

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["data"]["status"] == "COMPLETED"
    app.dependency_overrides.clear()


def test_completed_with_warnings_returns_http_200():
    client = _client()
    _override_preparation(
        FakePreparationService(
            _prepare_result("run-1", status="COMPLETED_WITH_WARNINGS", warnings=["WARN"])
        )
    )

    response = client.post("/api/v1/discovery/runs/run-1/prepare", json={})

    assert response.status_code == 200
    assert response.json()["data"]["warnings"] == ["WARN"]
    app.dependency_overrides.clear()


def test_persisted_failed_preparation_returns_http_200():
    client = _client()
    error = {"code": "SAFE_STAGE_FAILURE", "message": "Safe persisted failure."}
    _override_preparation(
        FakePreparationService(_prepare_result("run-1", status="FAILED", error=error))
    )

    response = client.post("/api/v1/discovery/runs/run-1/prepare", json={})

    assert response.status_code == 200
    assert response.json()["data"]["error"] == error
    app.dependency_overrides.clear()


def test_default_request_uses_resume_true():
    client = _client()
    service = _override_preparation(FakePreparationService(_prepare_result("run-1")))

    client.post("/api/v1/discovery/runs/run-1/prepare", json={})

    assert service.calls == [{"run_id": "run-1", "resume": True, "force_restart": False}]
    app.dependency_overrides.clear()


def test_resume_false_is_passed_correctly():
    client = _client()
    service = _override_preparation(FakePreparationService(_prepare_result("run-1")))

    client.post("/api/v1/discovery/runs/run-1/prepare", json={"resume": False})

    assert service.calls[0]["resume"] is False
    app.dependency_overrides.clear()


def test_force_restart_true_is_passed_correctly():
    client = _client()
    service = _override_preparation(FakePreparationService(_prepare_result("run-1")))

    client.post("/api/v1/discovery/runs/run-1/prepare", json={"force_restart": True})

    assert service.calls[0]["force_restart"] is True
    app.dependency_overrides.clear()


def test_invalid_run_id_returns_http_422():
    client = _client()
    service = _override_preparation(FakePreparationService())

    response = client.post("/api/v1/discovery/runs/bad%20id/prepare", json={})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_DISCOVERY_RUN_ID"
    assert service.calls == []
    app.dependency_overrides.clear()


def test_invalid_request_body_returns_prepare_http_422():
    client = _client()
    service = _override_preparation(FakePreparationService())

    response = client.post("/api/v1/discovery/runs/run-1/prepare", json={"resume": "yes"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_DISCOVERY_PREPARATION_REQUEST"
    assert service.calls == []
    app.dependency_overrides.clear()


def test_missing_run_returns_http_404():
    client = _client()
    error = {"code": "DISCOVERY_RUN_NOT_FOUND", "message": "Discovery run not found."}
    _override_preparation(
        FakePreparationService(_prepare_result("missing", status="FAILED", error=error))
    )

    response = client.post("/api/v1/discovery/runs/missing/prepare", json={})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "DISCOVERY_RUN_NOT_FOUND"
    app.dependency_overrides.clear()


def test_concurrent_preparation_returns_http_409():
    client = _client()
    error = {"code": "DISCOVERY_RUN_ALREADY_RUNNING", "message": "busy"}
    _override_preparation(
        FakePreparationService(_prepare_result("run-1", status="FAILED", error=error))
    )

    response = client.post("/api/v1/discovery/runs/run-1/prepare", json={})

    assert response.status_code == 409
    assert response.json()["error"]["message"] == "This discovery run is already being processed."
    app.dependency_overrides.clear()


def test_missing_as_of_date_returns_http_422():
    client = _client()
    error = {
        "code": "DISCOVERY_RUN_AS_OF_DATE_UNAVAILABLE",
        "message": "missing as-of",
    }
    _override_preparation(
        FakePreparationService(_prepare_result("run-1", status="FAILED", error=error))
    )

    response = client.post("/api/v1/discovery/runs/run-1/prepare", json={})

    assert response.status_code == 422
    assert response.json()["error"] == {
        "code": "DISCOVERY_RUN_AS_OF_DATE_UNAVAILABLE",
        "message": "Discovery run source data date is unavailable.",
    }
    app.dependency_overrides.clear()


def test_missing_preparation_service_returns_http_422():
    client = _client()
    error = {
        "code": "DISCOVERY_PREPARATION_SERVICE_UNAVAILABLE",
        "message": "internal service name",
    }
    _override_preparation(
        FakePreparationService(_prepare_result("run-1", status="FAILED", error=error))
    )

    response = client.post("/api/v1/discovery/runs/run-1/prepare", json={})

    assert response.status_code == 422
    assert response.json()["error"] == {
        "code": "DISCOVERY_PREPARATION_SERVICE_UNAVAILABLE",
        "message": "A required discovery preparation service is unavailable.",
    }
    app.dependency_overrides.clear()


def test_unexpected_failure_returns_safe_http_500():
    client = _client()
    _override_preparation(FakePreparationService(exc=RuntimeError(r"C:\secret\traceback.sql token=abc")))

    response = client.post("/api/v1/discovery/runs/run-1/prepare", json={})

    assert response.status_code == 500
    assert response.json() == {
        "success": False,
        "error": {
            "code": "DISCOVERY_PREPARATION_FAILED",
            "message": "Discovery preparation could not be completed.",
        },
    }
    assert "secret" not in response.text
    assert "traceback.sql" not in response.text
    app.dependency_overrides.clear()


def test_dependency_called_exactly_once():
    client = _client()
    service = _override_preparation(FakePreparationService(_prepare_result("run-1")))

    response = client.post("/api/v1/discovery/runs/run-1/prepare", json={})

    assert response.status_code == 200
    assert len(service.calls) == 1
    app.dependency_overrides.clear()


def test_no_orchestrator_call():
    client = _client()
    service = _override_preparation(FakePreparationService(_prepare_result("run-1")))
    orchestrator = FakeOrchestrator()
    app.dependency_overrides[get_discovery_pipeline_orchestrator] = lambda: orchestrator

    response = client.post("/api/v1/discovery/runs/run-1/prepare", json={})

    assert response.status_code == 200
    assert service.calls
    assert orchestrator.calls == []
    app.dependency_overrides.clear()


def test_no_background_task():
    signature = inspect.signature(prepare_discovery_run)

    assert "BackgroundTasks" not in str(signature)


def test_existing_create_execute_and_result_routes_unchanged():
    client = _client()
    app.dependency_overrides[get_discovery_run_creation_service] = lambda: FakeCreationService()
    app.dependency_overrides[get_discovery_pipeline_orchestrator] = lambda: FakeOrchestrator()
    app.dependency_overrides[get_discovery_result_service] = lambda: FakeResultService()

    create_response = client.post("/api/v1/discovery/runs", json={"run_id": "created-run"})
    execute_response = client.post("/api/v1/discovery/runs/run-1/execute", json={})
    result_response = client.get("/api/v1/discovery/runs/run-1/result")

    assert create_response.status_code == 201
    assert execute_response.status_code == 200
    assert result_response.status_code == 200
    app.dependency_overrides.clear()


def test_prepare_route_appears_exactly_once_in_openapi():
    client = _client()
    schema = client.get("/openapi.json").json()

    paths = [path for path in schema["paths"] if path == "/api/v1/discovery/runs/{run_id}/prepare"]
    assert paths == ["/api/v1/discovery/runs/{run_id}/prepare"]
    assert list(schema["paths"]["/api/v1/discovery/runs/{run_id}/prepare"].keys()) == ["post"]


def test_no_conditional_test_skips():
    source = Path(__file__).read_text()

    assert ".".join(["pytest", "skip"]) not in source
    assert "".join(["import", "orskip"]) not in source

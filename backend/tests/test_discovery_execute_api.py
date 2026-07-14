import inspect

from fastapi.testclient import TestClient
from sqlalchemy import event

from api.v1.discovery_router import (
    execute_discovery_run,
    get_discovery_pipeline_orchestrator,
    get_discovery_result_service,
)
from database import discovery_engine
from main import app


def _execute_result(run_id="run-123", status="COMPLETED", error=None, warnings=None):
    return {
        "run_id": run_id,
        "status": status,
        "last_completed_stage": "STOCK_RANKING" if status != "FAILED" else "SECTOR_RANKING",
        "resume_count": 0,
        "horizons": {"SHORT": {}, "MID": {}, "LONG": {}},
        "stage_results": {"STOCK_RANKING": {"status": status}},
        "warnings": warnings or [],
        "error": error,
    }


def _result_service_payload(run_id="run-123"):
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


class FakeOrchestrator:
    def __init__(self, result=None, exc=None):
        self.result = result
        self.exc = exc
        self.calls = []

    def execute(self, run_id, resume=True, force_restart=False):
        self.calls.append(
            {"run_id": run_id, "resume": resume, "force_restart": force_restart}
        )
        if self.exc is not None:
            raise self.exc
        return self.result or _execute_result(run_id)


class FakeResultService:
    def __init__(self, result=None):
        self.result = result or _result_service_payload()

    def get_result(self, run_id):
        result = dict(self.result)
        result["run_id"] = run_id
        return result


def _override_orchestrator(orchestrator):
    app.dependency_overrides[get_discovery_pipeline_orchestrator] = lambda: orchestrator
    return orchestrator


def _override_result_service(service):
    app.dependency_overrides[get_discovery_result_service] = lambda: service
    return service


def _client():
    app.dependency_overrides.clear()
    return TestClient(app)


def test_completed_execution_returns_http_200():
    client = _client()
    _override_orchestrator(FakeOrchestrator(_execute_result("run-1")))

    response = client.post("/api/v1/discovery/runs/run-1/execute", json={})

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["data"]["status"] == "COMPLETED"
    app.dependency_overrides.clear()


def test_completed_with_warnings_returns_http_200():
    client = _client()
    _override_orchestrator(
        FakeOrchestrator(_execute_result("run-1", status="COMPLETED_WITH_WARNINGS", warnings=["WARN"]))
    )

    response = client.post("/api/v1/discovery/runs/run-1/execute", json={})

    assert response.status_code == 200
    assert response.json()["data"]["warnings"] == ["WARN"]
    app.dependency_overrides.clear()


def test_persisted_failed_pipeline_result_returns_http_200():
    client = _client()
    error = {"code": "SAFE_CODE", "message": "Safe concise message."}
    _override_orchestrator(FakeOrchestrator(_execute_result("run-1", status="FAILED", error=error)))

    response = client.post("/api/v1/discovery/runs/run-1/execute", json={})

    assert response.status_code == 200
    assert response.json()["data"]["error"] == error
    app.dependency_overrides.clear()


def test_default_request_uses_resume_true():
    client = _client()
    orchestrator = _override_orchestrator(FakeOrchestrator(_execute_result("run-1")))

    client.post("/api/v1/discovery/runs/run-1/execute", json={})

    assert orchestrator.calls == [{"run_id": "run-1", "resume": True, "force_restart": False}]
    app.dependency_overrides.clear()


def test_resume_false_is_passed_correctly():
    client = _client()
    orchestrator = _override_orchestrator(FakeOrchestrator(_execute_result("run-1")))

    client.post("/api/v1/discovery/runs/run-1/execute", json={"resume": False})

    assert orchestrator.calls[0]["resume"] is False
    app.dependency_overrides.clear()


def test_force_restart_true_is_passed_correctly():
    client = _client()
    orchestrator = _override_orchestrator(FakeOrchestrator(_execute_result("run-1")))

    client.post("/api/v1/discovery/runs/run-1/execute", json={"force_restart": True})

    assert orchestrator.calls[0]["force_restart"] is True
    app.dependency_overrides.clear()


def test_run_id_whitespace_is_trimmed():
    client = _client()
    orchestrator = _override_orchestrator(FakeOrchestrator(_execute_result("run_trim")))

    response = client.post("/api/v1/discovery/runs/%20run_trim%20/execute", json={})

    assert response.status_code == 200
    assert orchestrator.calls[0]["run_id"] == "run_trim"
    app.dependency_overrides.clear()


def test_invalid_run_id_returns_http_422():
    client = _client()
    orchestrator = _override_orchestrator(FakeOrchestrator())

    response = client.post("/api/v1/discovery/runs/bad%20id/execute", json={})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_DISCOVERY_RUN_ID"
    assert orchestrator.calls == []
    app.dependency_overrides.clear()


def test_missing_run_returns_http_404():
    client = _client()
    error = {"code": "DISCOVERY_RUN_NOT_FOUND", "message": "Discovery run not found."}
    _override_orchestrator(FakeOrchestrator(_execute_result("missing", status="FAILED", error=error)))

    response = client.post("/api/v1/discovery/runs/missing/execute", json={})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "DISCOVERY_RUN_NOT_FOUND"
    app.dependency_overrides.clear()


def test_concurrent_execution_returns_http_409():
    client = _client()
    error = {"code": "DISCOVERY_RUN_ALREADY_RUNNING", "message": "busy"}
    _override_orchestrator(FakeOrchestrator(_execute_result("run-1", status="FAILED", error=error)))

    response = client.post("/api/v1/discovery/runs/run-1/execute", json={})

    assert response.status_code == 409
    assert response.json()["error"]["message"] == "This discovery run is already being processed."
    app.dependency_overrides.clear()


def test_missing_upstream_data_returns_http_422():
    client = _client()
    error = {"code": "DISCOVERY_UPSTREAM_DATA_UNAVAILABLE", "message": "table details"}
    _override_orchestrator(FakeOrchestrator(_execute_result("run-1", status="FAILED", error=error)))

    response = client.post("/api/v1/discovery/runs/run-1/execute", json={})

    assert response.status_code == 422
    assert response.json()["error"] == {
        "code": "DISCOVERY_UPSTREAM_DATA_UNAVAILABLE",
        "message": "Required upstream discovery data is unavailable.",
    }
    app.dependency_overrides.clear()


def test_unexpected_orchestrator_failure_returns_safe_http_500():
    client = _client()
    _override_orchestrator(FakeOrchestrator(exc=RuntimeError("database exploded")))

    response = client.post("/api/v1/discovery/runs/run-1/execute", json={})

    assert response.status_code == 500
    assert response.json() == {
        "success": False,
        "error": {
            "code": "DISCOVERY_PIPELINE_EXECUTION_FAILED",
            "message": "The discovery pipeline could not be executed.",
        },
    }
    app.dependency_overrides.clear()


def test_stack_trace_is_not_exposed():
    client = _client()
    _override_orchestrator(FakeOrchestrator(exc=RuntimeError("Traceback secret")))

    response = client.post("/api/v1/discovery/runs/run-1/execute", json={})

    assert "Traceback" not in response.text
    assert "secret" not in response.text
    app.dependency_overrides.clear()


def test_internal_file_paths_are_not_exposed():
    client = _client()
    _override_orchestrator(FakeOrchestrator(exc=RuntimeError(r"C:\Users\Yug\Desktop\mix\backend\secret.py")))

    response = client.post("/api/v1/discovery/runs/run-1/execute", json={})

    assert "C:\\Users" not in response.text
    assert "secret.py" not in response.text
    app.dependency_overrides.clear()


def test_orchestrator_is_called_exactly_once():
    client = _client()
    orchestrator = _override_orchestrator(FakeOrchestrator(_execute_result("run-1")))

    response = client.post("/api/v1/discovery/runs/run-1/execute", json={})

    assert response.status_code == 200
    assert len(orchestrator.calls) == 1
    app.dependency_overrides.clear()


def test_route_does_not_directly_query_discovery_tables():
    client = _client()
    _override_orchestrator(FakeOrchestrator(_execute_result("run-1")))
    statements = []

    @event.listens_for(discovery_engine, "before_cursor_execute")
    def intercept(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    try:
        client.post("/api/v1/discovery/runs/run-1/execute", json={})
    finally:
        event.remove(discovery_engine, "before_cursor_execute", intercept)
        app.dependency_overrides.clear()

    assert statements == []


def test_route_does_not_manually_delete_or_reset_derived_rows():
    client = _client()
    _override_orchestrator(FakeOrchestrator(_execute_result("run-1")))
    writes = []

    @event.listens_for(discovery_engine, "before_cursor_execute")
    def intercept(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith(("DELETE", "UPDATE", "INSERT")):
            writes.append(statement)

    try:
        client.post("/api/v1/discovery/runs/run-1/execute", json={"force_restart": True})
    finally:
        event.remove(discovery_engine, "before_cursor_execute", intercept)
        app.dependency_overrides.clear()

    assert writes == []


def test_no_background_task_is_created():
    signature = inspect.signature(execute_discovery_run)

    assert "BackgroundTasks" not in str(signature)


def test_request_waits_for_orchestrator_result():
    client = _client()
    orchestrator = _override_orchestrator(FakeOrchestrator(_execute_result("run-1", warnings=["DONE"])))

    response = client.post("/api/v1/discovery/runs/run-1/execute", json={})

    assert orchestrator.calls
    assert response.json()["data"]["warnings"] == ["DONE"]
    app.dependency_overrides.clear()


def test_existing_result_get_endpoint_remains_unaffected():
    client = _client()
    _override_result_service(FakeResultService())

    response = client.get("/api/v1/discovery/runs/run-1/result")

    assert response.status_code == 200
    assert response.json()["success"] is True
    app.dependency_overrides.clear()


def test_execute_endpoint_appears_exactly_once_in_openapi():
    client = _client()
    schema = client.get("/openapi.json").json()

    paths = [path for path in schema["paths"] if path == "/api/v1/discovery/runs/{run_id}/execute"]
    assert paths == ["/api/v1/discovery/runs/{run_id}/execute"]


def test_openapi_method_is_post():
    client = _client()
    schema = client.get("/openapi.json").json()

    assert list(schema["paths"]["/api/v1/discovery/runs/{run_id}/execute"].keys()) == ["post"]


def test_invalid_body_returns_safe_http_422():
    client = _client()
    _override_orchestrator(FakeOrchestrator())

    response = client.post("/api/v1/discovery/runs/run-1/execute", json={"resume": "yes"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_DISCOVERY_REQUEST"
    app.dependency_overrides.clear()

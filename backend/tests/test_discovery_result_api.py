import pytest
from sqlalchemy import event

from database import discovery_engine
from fastapi.testclient import TestClient

from api.v1.discovery_router import (
    get_discovery_result_service,
    get_discovery_session,
)
from main import app


def _sample_result(run_id="run-123", status="COMPLETED", run_error=None, warnings=None, horizon_status="COMPLETED"):
    return {
        "run_id": run_id,
        "status": status,
        "current_stage": None if status != "RUNNING" else "SECTOR_RANKING",
        "last_completed_stage": "STOCK_RANKING",
        "started_at": "2026-07-13T10:00:00Z",
        "completed_at": "2026-07-13T10:05:00Z" if status != "RUNNING" else None,
        "resume_count": 0,
        "warnings": warnings or [],
        "error": run_error,
        "stage_results": {"STOCK_RANKING": {"status": horizon_status}},
        "horizons": {
            "SHORT": {
                "status": horizon_status,
                "sector": {
                    "name": "Technology",
                    "rank": 1,
                    "final_score": 78.25,
                    "technical_score": 80,
                    "fundamental_score": 75,
                    "macro_score": 79,
                    "status": "STRONG",
                    "coverage_pct": 100,
                    "warnings": [],
                },
                "industry": {
                    "name": "Software",
                    "parent_sector": "Technology",
                    "rank": 1,
                    "final_score": 81.5,
                    "technical_score": 84,
                    "fundamental_score": 78,
                    "macro_score": 82,
                    "status": "VERY_STRONG",
                    "coverage_pct": 100,
                    "warnings": [],
                },
                "basic_industry": {
                    "name": "Enterprise Software",
                    "parent_sector": "Technology",
                    "parent_industry": "Software",
                    "rank": 1,
                    "final_score": 83,
                    "technical_score": 86,
                    "fundamental_score": 80,
                    "macro_score": 81,
                    "status": "VERY_STRONG",
                    "coverage_pct": 100,
                    "warnings": [],
                },
                "stocks": [
                    {
                        "company_id": "company-1",
                        "symbol": "AAA",
                        "rank": 1,
                        "selected": True,
                        "final_score": 84,
                        "technical_score": 88,
                        "fundamental_score": 80,
                        "inherited_macro_score": 81,
                        "score_status": "VERY_STRONG",
                        "score_coverage_pct": 100,
                        "warnings": [],
                    },
                    {
                        "company_id": "company-2",
                        "symbol": "BBB",
                        "rank": 2,
                        "selected": True,
                        "final_score": 80,
                        "technical_score": 82,
                        "fundamental_score": 78,
                        "inherited_macro_score": 81,
                        "score_status": "STRONG",
                        "score_coverage_pct": 95,
                        "warnings": [],
                    },
                ],
                "warnings": [],
            },
            "MID": {"status": "PENDING", "sector": None, "industry": None, "basic_industry": None, "stocks": [], "warnings": []},
            "LONG": {"status": "PENDING", "sector": None, "industry": None, "basic_industry": None, "stocks": [], "warnings": []},
        },
    }


class FakeService:
    def __init__(self, result=None, exc=None):
        self.result = result
        self.exc = exc
        self.calls = []

    def get_result(self, run_id):
        self.calls.append(run_id)
        if self.exc is not None:
            raise self.exc
        return self.result or _sample_result(run_id)


@pytest.fixture
def client():
    app.dependency_overrides.clear()
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _override_service(service):
    app.dependency_overrides[get_discovery_result_service] = lambda: service
    return service


def test_completed_run_returns_http_200(client):
    _override_service(FakeService(_sample_result("run-1")))

    response = client.get("/api/v1/discovery/runs/run-1/result")

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["data"]["status"] == "COMPLETED"


def test_pending_run_returns_http_200(client):
    _override_service(FakeService(_sample_result("run-1", status="PENDING")))

    response = client.get("/api/v1/discovery/runs/run-1/result")

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "PENDING"


def test_running_partial_run_returns_http_200(client):
    _override_service(FakeService(_sample_result("run-1", status="RUNNING", horizon_status="RUNNING")))

    response = client.get("/api/v1/discovery/runs/run-1/result")

    assert response.status_code == 200
    assert response.json()["data"]["horizons"]["SHORT"]["status"] == "RUNNING"


def test_failed_persisted_run_returns_http_200_with_safe_error(client):
    error = {"code": "SAFE_CODE", "message": "Safe failure"}
    _override_service(FakeService(_sample_result("run-1", status="FAILED", run_error=error)))

    response = client.get("/api/v1/discovery/runs/run-1/result")

    assert response.status_code == 200
    assert response.json()["data"]["error"] == error


def test_completed_with_warnings_run_returns_http_200(client):
    _override_service(FakeService(_sample_result("run-1", status="COMPLETED_WITH_WARNINGS", warnings=["WARN"])))

    response = client.get("/api/v1/discovery/runs/run-1/result")

    assert response.status_code == 200
    assert response.json()["data"]["warnings"] == ["WARN"]


def test_short_mid_and_long_are_returned(client):
    _override_service(FakeService(_sample_result("run-1")))

    response = client.get("/api/v1/discovery/runs/run-1/result")

    assert list(response.json()["data"]["horizons"]) == ["SHORT", "MID", "LONG"]


def test_multiple_selected_stocks_remain_rank_ordered(client):
    _override_service(FakeService(_sample_result("run-1")))

    response = client.get("/api/v1/discovery/runs/run-1/result")

    stocks = response.json()["data"]["horizons"]["SHORT"]["stocks"]
    assert [stock["symbol"] for stock in stocks] == ["AAA", "BBB"]


def test_missing_run_returns_http_404(client):
    result = _sample_result("missing", status="FAILED", run_error={"code": "DISCOVERY_RUN_NOT_FOUND", "message": "Discovery run not found."})
    _override_service(FakeService(result))

    response = client.get("/api/v1/discovery/runs/missing/result")

    assert response.status_code == 404
    assert response.json() == {
        "success": False,
        "error": {
            "code": "DISCOVERY_RUN_NOT_FOUND",
            "message": "Discovery run was not found.",
        },
    }


def test_invalid_run_id_returns_http_422(client):
    service = _override_service(FakeService())

    response = client.get("/api/v1/discovery/runs/bad%20id/result")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_DISCOVERY_RUN_ID"
    assert service.calls == []


def test_whitespace_is_trimmed(client):
    service = _override_service(FakeService(_sample_result("run_trim")))

    response = client.get("/api/v1/discovery/runs/%20run_trim%20/result")

    assert response.status_code == 200
    assert service.calls == ["run_trim"]


def test_run_id_longer_than_128_characters_is_rejected(client):
    service = _override_service(FakeService())

    response = client.get(f"/api/v1/discovery/runs/{'a' * 129}/result")

    assert response.status_code == 422
    assert service.calls == []


def test_unexpected_service_failure_returns_safe_http_500(client):
    _override_service(FakeService(exc=RuntimeError("database exploded")))

    response = client.get("/api/v1/discovery/runs/run-1/result")

    assert response.status_code == 500
    assert response.json() == {
        "success": False,
        "error": {
            "code": "DISCOVERY_RESULT_UNAVAILABLE",
            "message": "The discovery result could not be loaded.",
        },
    }


def test_stack_trace_is_not_exposed(client):
    _override_service(FakeService(exc=RuntimeError("Traceback (most recent call last): secret")))

    response = client.get("/api/v1/discovery/runs/run-1/result")

    assert "Traceback" not in response.text
    assert "secret" not in response.text


def test_internal_file_paths_are_not_exposed(client):
    _override_service(FakeService(exc=RuntimeError(r"C:\Users\Yug\Desktop\mix\backend\secret.py")))

    response = client.get("/api/v1/discovery/runs/run-1/result")

    assert "C:\\Users" not in response.text
    assert "secret.py" not in response.text


def test_service_is_called_exactly_once(client):
    service = _override_service(FakeService(_sample_result("run-1")))

    response = client.get("/api/v1/discovery/runs/run-1/result")

    assert response.status_code == 200
    assert service.calls == ["run-1"]


def test_route_does_not_directly_query_discovery_tables(client):
    _override_service(FakeService(_sample_result("run-1")))
    statements = []

    @event.listens_for(discovery_engine, "before_cursor_execute")
    def intercept(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    try:
        client.get("/api/v1/discovery/runs/run-1/result")
    finally:
        event.remove(discovery_engine, "before_cursor_execute", intercept)

    assert statements == []


def test_orchestrator_is_not_called(client, monkeypatch):
    called = []
    _override_service(FakeService(_sample_result("run-1")))

    def fail_execute(*args, **kwargs):
        called.append(True)
        raise AssertionError("orchestrator called")

    monkeypatch.setattr(
        "services.discovery.discovery_pipeline_orchestrator.DiscoveryPipelineOrchestrator.execute",
        fail_execute,
    )
    response = client.get("/api/v1/discovery/runs/run-1/result")

    assert response.status_code == 200
    assert called == []


def test_no_database_write_or_commit_occurs(client, monkeypatch):
    _override_service(FakeService(_sample_result("run-1")))
    app.dependency_overrides[get_discovery_session] = lambda: (_ for _ in ()).throw(AssertionError("session requested"))

    response = client.get("/api/v1/discovery/runs/run-1/result")

    assert response.status_code == 200


def test_no_parallel_or_llm_call_occurs(client):
    service = _override_service(FakeService(_sample_result("run-1")))

    response = client.get("/api/v1/discovery/runs/run-1/result")

    assert response.status_code == 200
    assert not hasattr(service, "_llm")
    assert not hasattr(service, "_parallel")
    assert not hasattr(service, "_provider")


def test_route_registered_exactly_once_in_openapi(client):
    schema = client.get("/openapi.json").json()

    paths = [path for path in schema["paths"] if path == "/api/v1/discovery/runs/{run_id}/result"]
    assert paths == ["/api/v1/discovery/runs/{run_id}/result"]


def test_existing_api_routes_remain_unaffected(client):
    schema = client.get("/openapi.json").json()

    assert "/openapi.json" not in schema["paths"]
    assert "/api/v1/discovery/runs/{run_id}/result" in schema["paths"]

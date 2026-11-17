from __future__ import annotations

import json
import sqlite3

import pytest
from auth_helpers import configure_test_access
from fastapi.testclient import TestClient

from app.execution import (
    DeterministicFakeProvider,
    ExecutionService,
    ProviderError,
    ProviderResponse,
)
from app.main import create_app


def manifest():
    path = __import__("pathlib").Path(__file__).resolve().parents[2] / "examples" / "agent-manifest-v1.json"
    value = json.loads(path.read_text())
    value["agent_id"] = "runtime.test"
    value["model_binding"]["provider_id"] = "provider.fake"
    value["tools"] = []
    return value


@pytest.fixture
def client(tmp_path):
    app = configure_test_access(create_app(str(tmp_path / "runtime.sqlite3")))
    app.state.executions.providers["provider.fake"] = DeterministicFakeProvider()
    return TestClient(app)


def create_agent(client):
    response = client.post("/v1/agents", json={"manifest": manifest()})
    assert response.status_code == 201
    return "runtime.test", "1.0.0"


def test_execution_database_connections_close_after_context(client):
    connection = client.app.state.executions._connect()
    with connection:
        connection.execute("SELECT 1")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")


def test_execute_persists_output_and_usage(client):
    agent_id, version = create_agent(client)
    response = client.post(
        f"/v1/agents/{agent_id}/versions/{version}/executions",
        headers={"Idempotency-Key": "exec-1"},
        json={"input": {"question": "hello"}, "max_output_tokens": 128},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "completed"
    assert body["result"]["output"]["input"] == {"question": "hello"}
    assert body["result"]["steps"] == 1
    fetched = client.get(f"/v1/executions/{body['execution_id']}")
    assert fetched.json() == body


def test_interrupted_provider_call_is_fenced_on_restart_without_replay(client, tmp_path):
    agent_id, version = create_agent(client)

    class ProcessCrashProvider:
        provider_id = "provider.fake"

        def invoke(self, request, timeout_seconds):
            del request, timeout_seconds
            raise SystemExit("simulated process termination")

    class MustNotReplayProvider:
        provider_id = "provider.fake"

        def __init__(self):
            self.calls = 0

        def invoke(self, request, timeout_seconds):
            del request, timeout_seconds
            self.calls += 1
            return ProviderResponse(output="replayed")

    first_service = client.app.state.executions
    first_service.providers["provider.fake"] = ProcessCrashProvider()
    with pytest.raises(SystemExit, match="simulated process termination"):
        first_service.execute(
            agent_id, version, "charge-once", "crash-key", tenant_id="test-tenant"
        )

    safe_provider = MustNotReplayProvider()
    restarted_service = ExecutionService(
        tmp_path / "runtime.sqlite3",
        client.app.state.registry,
        providers=[safe_provider],
    )
    replay = restarted_service.execute(
        agent_id, version, "charge-once", "crash-key", tenant_id="test-tenant"
    )

    assert replay["status"] == "interrupted"
    assert replay["error_code"] == "execution_interrupted_ambiguous_outcome"
    assert replay["completed_at"] is not None
    assert safe_provider.calls == 0


def test_idempotency_returns_same_execution(client):
    agent_id, version = create_agent(client)
    url = f"/v1/agents/{agent_id}/versions/{version}/executions"
    headers = {"Idempotency-Key": "same-key"}
    first = client.post(url, headers=headers, json={"input": "x"}).json()
    second = client.post(url, headers=headers, json={"input": "x"}).json()
    assert first["execution_id"] == second["execution_id"]


def test_idempotency_conflict_rejected(client):
    agent_id, version = create_agent(client)
    url = f"/v1/agents/{agent_id}/versions/{version}/executions"
    headers = {"Idempotency-Key": "same-key"}
    assert client.post(url, headers=headers, json={"input": "x"}).status_code == 201
    assert client.post(url, headers=headers, json={"input": "different"}).status_code == 409


def test_tool_enabled_agent_denied(client):
    value = manifest()
    value["tools"] = [{
        "tool_id": "tool.example", "version_range": "^1.0.0",
        "permission_profile": "read-only"
    }]
    assert client.post("/v1/agents", json={"manifest": value}).status_code == 201
    response = client.post(
        "/v1/agents/runtime.test/versions/1.0.0/executions",
        headers={"Idempotency-Key": "tool-deny"},
        json={"input": "x"},
    )
    assert response.status_code == 403


def test_unknown_provider_denied(client):
    value = manifest()
    value["model_binding"]["provider_id"] = "provider.not-configured"
    assert client.post("/v1/agents", json={"manifest": value}).status_code == 201
    response = client.post(
        "/v1/agents/runtime.test/versions/1.0.0/executions",
        headers={"Idempotency-Key": "no-provider"},
        json={"input": "x"},
    )
    assert response.status_code == 503


def test_execution_limits_validated(client):
    agent_id, version = create_agent(client)
    response = client.post(
        f"/v1/agents/{agent_id}/versions/{version}/executions",
        headers={"Idempotency-Key": "bad-limit"},
        json={"input": "x", "max_output_tokens": 90000},
    )
    assert response.status_code == 422


def test_execution_not_found(client):
    assert client.get("/v1/executions/not-real").status_code == 404


@pytest.mark.parametrize("metrics", [
    {"input_tokens": True, "output_tokens": 1, "cost_usd": 0.0},
    {"input_tokens": 1.5, "output_tokens": 1, "cost_usd": 0.0},
    {"input_tokens": 1, "output_tokens": -1, "cost_usd": 0.0},
    {"input_tokens": 1, "output_tokens": 1, "cost_usd": float("nan")},
    {"input_tokens": 1, "output_tokens": 1, "cost_usd": float("inf")},
    {"input_tokens": 1, "output_tokens": 1, "cost_usd": True},
])
def test_execution_rejects_malformed_provider_usage_and_marks_failed(client, metrics):
    class InvalidMetricsProvider:
        provider_id = "provider.fake"

        def invoke(self, request, timeout_seconds):
            del request, timeout_seconds
            return ProviderResponse(output="unsafe metrics", **metrics)

    create_agent(client)
    client.app.state.executions.providers["provider.fake"] = InvalidMetricsProvider()
    with pytest.raises(ProviderError, match="invalid usage metrics"):
        client.app.state.executions.execute(
            "runtime.test", "1.0.0", "test", "invalid-metrics", tenant_id="test-tenant"
        )

    with client.app.state.executions._connect() as connection:
        row = connection.execute(
            "SELECT status, error_code FROM executions WHERE tenant_id=?",
            ("test-tenant",),
        ).fetchone()
    assert row["status"] == "failed"
    assert row["error_code"] == "provider_error"

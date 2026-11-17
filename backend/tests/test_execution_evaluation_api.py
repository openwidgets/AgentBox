from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from auth_helpers import configure_test_access
from fastapi.testclient import TestClient

from app.execution import ProviderResponse
from app.main import create_app

ROOT = Path(__file__).resolve().parents[2]
AGENT = json.loads((ROOT / "examples" / "agent-manifest-v1.json").read_text(encoding="utf-8"))
PROFILE = json.loads((ROOT / "examples" / "evaluation-profile-v1.json").read_text(encoding="utf-8"))


class EvaluationProvider:
    provider_id = "provider.fake"

    def invoke(self, request, timeout_seconds):
        del timeout_seconds
        return ProviderResponse(
            output={
                "summary": "A technical summary",
                "conclusion": "The conclusion is supported by the source.",
                "sources": ["source://report/1"],
            },
            input_tokens=12,
            output_tokens=18,
            cost_usd=0.001,
        )


def client_for(tmp_path: Path) -> TestClient:
    app = configure_test_access(create_app(str(tmp_path / "execution-evaluation.sqlite3")))
    app.state.executions.providers["provider.fake"] = EvaluationProvider()
    return TestClient(app)


def test_evaluation_executes_profile_cases_and_persists_execution_evidence(
    tmp_path: Path,
) -> None:
    client = client_for(tmp_path)
    manifest = copy.deepcopy(AGENT)
    manifest["agent_id"] = "runtime.test"
    manifest["model_binding"]["provider_id"] = "provider.fake"
    manifest["tools"] = []
    assert client.post("/v1/agents", json={"manifest": manifest}).status_code == 201
    assert client.post("/v1/evaluation-profiles", json={"profile": PROFILE}).status_code == 201

    response = client.post(
        "/v1/agents/runtime.test/versions/1.0.0/evaluations/execute",
        headers={"Idempotency-Key": "trusted-eval-run-001"},
        json={
            "profile_id": PROFILE["profile_id"],
            "profile_version": PROFILE["version"],
            "max_output_tokens": 128,
        },
    )
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["evidence_status"] == "server_recorded"
    evidence = result["execution_evidence"]
    assert evidence["source"] == "server_execution_records"
    assert evidence["agent_content_hash"]
    assert evidence["profile_content_hash"]
    assert evidence["workload_hash"]
    assert len(evidence["case_executions"]) == len(PROFILE["cases"])
    assert result["passed"] is True
    for item in evidence["case_executions"]:
        assert item["execution_id"]
        assert item["input_hash"]
        assert item["output_hash"]
        assert item["provider_id"] == "provider.fake"
        execution = client.get(f"/v1/executions/{item['execution_id']}")
        assert execution.status_code == 200
        assert execution.json()["status"] == "completed"


def test_execution_backed_evaluation_reuses_per_case_execution_on_retry(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    manifest = copy.deepcopy(AGENT)
    manifest["agent_id"] = "runtime.test"
    manifest["model_binding"]["provider_id"] = "provider.fake"
    manifest["tools"] = []
    assert client.post("/v1/agents", json={"manifest": manifest}).status_code == 201
    assert client.post("/v1/evaluation-profiles", json={"profile": PROFILE}).status_code == 201
    url = "/v1/agents/runtime.test/versions/1.0.0/evaluations/execute"
    headers = {"Idempotency-Key": "trusted-eval-retry-001"}
    payload = {"profile_id": PROFILE["profile_id"], "profile_version": PROFILE["version"]}
    first = client.post(url, headers=headers, json=payload)
    second = client.post(url, headers=headers, json=payload)
    assert first.status_code == second.status_code == 201
    first_ids = [
        item["execution_id"] for item in first.json()["execution_evidence"]["case_executions"]
    ]
    second_ids = [
        item["execution_id"] for item in second.json()["execution_evidence"]["case_executions"]
    ]
    assert first_ids == second_ids


def test_execution_backed_evaluation_rejects_tool_enabled_agent(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    manifest = copy.deepcopy(AGENT)
    manifest["agent_id"] = "runtime.test"
    manifest["model_binding"]["provider_id"] = "provider.fake"
    manifest["tools"] = [{
        "tool_id": "tool.example", "version_range": "^1.0.0",
        "permission_profile": "read-only",
    }]
    assert client.post("/v1/agents", json={"manifest": manifest}).status_code == 201
    assert client.post("/v1/evaluation-profiles", json={"profile": PROFILE}).status_code == 201
    response = client.post(
        "/v1/agents/runtime.test/versions/1.0.0/evaluations/execute",
        headers={"Idempotency-Key": "tool-eval-denied"},
        json={"profile_id": PROFILE["profile_id"], "profile_version": PROFILE["version"]},
    )
    assert response.status_code == 403


def test_execution_evidence_tampering_is_rejected_before_scoring(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    manifest = copy.deepcopy(AGENT)
    manifest["agent_id"] = "runtime.test"
    manifest["model_binding"]["provider_id"] = "provider.fake"
    manifest["tools"] = []
    assert client.post("/v1/agents", json={"manifest": manifest}).status_code == 201
    assert client.post("/v1/evaluation-profiles", json={"profile": PROFILE}).status_code == 201
    response = client.post(
        "/v1/agents/runtime.test/versions/1.0.0/evaluations/execute",
        headers={"Idempotency-Key": "tamper-evidence-001"},
        json={"profile_id": PROFILE["profile_id"], "profile_version": PROFILE["version"]},
    )
    assert response.status_code == 201, response.text
    run = response.json()
    evidence = copy.deepcopy(run["execution_evidence"])
    evidence["case_executions"][0]["output_hash"] = "0" * 64
    submitted = []
    for item in evidence["case_executions"]:
        execution = client.app.state.executions.get(item["execution_id"], "test-tenant")
        result = execution["result"]
        submitted.append({
            "case_id": item["case_id"],
            "output": result["output"],
            "latency_ms": result["latency_ms"],
            "cost_usd": result["usage"]["cost_usd"],
        })
    with pytest.raises(ValueError, match="does not match its persisted execution"):
        client.app.state.evaluations.create_run(
            "runtime.test", "1.0.0", PROFILE["profile_id"], PROFILE["version"],
            submitted, "test-tenant", execution_evidence=evidence,
        )


def test_public_evaluation_endpoint_rejects_caller_supplied_execution_evidence(
    tmp_path: Path,
) -> None:
    client = client_for(tmp_path)
    manifest = copy.deepcopy(AGENT)
    manifest["agent_id"] = "runtime.test"
    assert client.post("/v1/agents", json={"manifest": manifest}).status_code == 201
    assert client.post("/v1/evaluation-profiles", json={"profile": PROFILE}).status_code == 201
    response = client.post(
        "/v1/agents/runtime.test/versions/1.0.0/evaluations",
        json={
            "profile_id": PROFILE["profile_id"],
            "profile_version": PROFILE["version"],
            "results": [],
            "execution_evidence": {"source": "server_execution_records"},
        },
    )
    assert response.status_code == 422

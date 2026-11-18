from __future__ import annotations

import copy
import json
from pathlib import Path

from auth_helpers import configure_test_access
from fastapi.testclient import TestClient

from app.main import create_app

ROOT = Path(__file__).resolve().parents[2]
AGENT = json.loads((ROOT / "examples" / "agent-manifest-v1.json").read_text(encoding="utf-8"))


def make_client(tmp_path):
    app = configure_test_access(create_app(str(tmp_path / "context-api.sqlite3")))
    client = TestClient(app)
    manifest = copy.deepcopy(AGENT)
    manifest["agent_id"] = "runtime.test"
    manifest["tools"] = []
    created = client.post("/v1/agents", json={"manifest": manifest})
    assert created.status_code == 201, created.text
    run = client.post(
        "/v1/cognitive-runs",
        headers={"Idempotency-Key": "context-run-create"},
        json={
            "goal_ref": "goal:context-test@1.0.0",
            "agent_ref": f'{manifest["agent_id"]}@{manifest["version"]}',
        },
    )
    assert run.status_code == 201, run.text
    return app, client, run.json()["run"]["run_id"]


def test_context_assembly_api_appends_snapshot_to_cognitive_run(tmp_path):
    app, client, run_id = make_client(tmp_path)
    app.state.enterprise_context.create_entity(
        {
            "entity_id": "app.alpha",
            "entity_type": "application",
            "name": "Alpha Application",
            "description": "Production application inventory record",
            "attributes": {"owner": "platform"},
            "assertion_class": "observed_fact",
            "confidence": 0.95,
        },
        "test-tenant", "test-user",
    )
    headers = {"Idempotency-Key": "assemble-context-1"}
    payload = {"entity_ids": ["app.alpha"]}
    first = client.post(
        f"/v1/cognitive-runs/{run_id}/context", headers=headers, json=payload
    )
    assert first.status_code == 201, first.text
    result = first.json()
    assert result["created"] is True
    assert result["event"]["stage"] == "context"
    assert result["event"]["event_type"] == "context.assembled"
    assert result["event"]["payload"]["context_content_hash"] == result["content_hash"]
    assert result["snapshot"]["entities"][0]["entity_id"] == "app.alpha"
    replay = client.post(
        f"/v1/cognitive-runs/{run_id}/context", headers=headers, json=payload
    )
    assert replay.status_code == 201, replay.text
    assert replay.json()["created"] is False
    assert replay.json()["event"]["event_id"] == result["event"]["event_id"]


def test_context_assembly_api_rejects_unknown_entities_and_ambiguous_selection(tmp_path):
    _, client, run_id = make_client(tmp_path)
    missing = client.post(
        f"/v1/cognitive-runs/{run_id}/context",
        headers={"Idempotency-Key": "missing-context"},
        json={"entity_ids": ["app.missing"]},
    )
    assert missing.status_code == 404
    ambiguous = client.post(
        f"/v1/cognitive-runs/{run_id}/context",
        headers={"Idempotency-Key": "ambiguous-context"},
        json={"entity_ids": ["app.alpha"], "search": "alpha"},
    )
    assert ambiguous.status_code == 422


def test_context_assembly_api_requires_authorization_and_idempotency_key(tmp_path):
    app, client, run_id = make_client(tmp_path)
    original_policies = app.state.authorization_policies
    app.state.authorization_policies = []
    denied = client.post(
        f"/v1/cognitive-runs/{run_id}/context",
        headers={"Idempotency-Key": "denied-context"},
        json={},
    )
    assert denied.status_code == 403
    app.state.authorization_policies = original_policies
    missing_key = client.post(f"/v1/cognitive-runs/{run_id}/context", json={})
    assert missing_key.status_code == 422


def test_context_assembly_api_requires_existing_context_read_grants(tmp_path):
    app, client, run_id = make_client(tmp_path)
    original_policies = app.state.authorization_policies
    app.state.authorization_policies = [
        policy for policy in original_policies
        if not (
            policy["capability_id"] == "enterprise.context"
            and policy["actions"][0] in {
                "context.entity.read",
                "context.relationship.read",
                "context.evidence.read",
            }
        )
    ]
    response = client.post(
        f"/v1/cognitive-runs/{run_id}/context",
        headers={"Idempotency-Key": "missing-context-read-grants"},
        json={},
    )
    assert response.status_code == 403
    app.state.authorization_policies = original_policies

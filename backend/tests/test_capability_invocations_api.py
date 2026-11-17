from __future__ import annotations

import json

from auth_helpers import configure_test_access
from fastapi.testclient import TestClient

from app.main import create_app


HANDLER = {
    "workload_kind": "provider_invocation",
    "handler_id": "provider.fake",
    "handler_version": "1.0.0",
}


def make_client(tmp_path, monkeypatch, configured=True):
    monkeypatch.setenv("AGENTBOX_JOB_ALLOWED_HANDLERS", json.dumps([HANDLER]))
    bindings = [{
        "capability_id": "crm.contact.read",
        "version": "1.0.0",
        **HANDLER,
    }] if configured else []
    monkeypatch.setenv("AGENTBOX_CAPABILITY_HANDLERS", json.dumps(bindings))
    app = configure_test_access(create_app(str(tmp_path / "capability-invocations.sqlite3")))
    app.state.authorization_policies.append({
        "schema_version": "1.0.0",
        "policy_id": "test.capability.invoke",
        "version": "1.0.0",
        "active": True,
        "effect": "allow",
        "capability_id": "crm.contact.read",
        "principal_scope": "user",
        "principal_ids": ["test-user"],
        "tenant_ids": ["test-tenant"],
        "actions": ["crm.contact.read"],
        "resource_ids": ["crm.contact.read"],
        "created_at": "2026-11-12T00:00:00+00:00",
    })
    client = TestClient(app)
    contract = {
        "schema_version": "1.0.0",
        "capability_id": "crm.contact.read",
        "version": "1.0.0",
        "name": "Read contact",
        "description": "Read a tenant-owned contact record.",
        "input_schema": {
            "type": "object",
            "properties": {"contact_id": {"type": "string", "minLength": 1}},
            "required": ["contact_id"],
            "additionalProperties": False,
        },
        "output_schema": {"type": "object"},
        "auth_ref": "policy:crm.contact.read",
        "permission_profile": {
            "principal_scope": "user",
            "actions": ["crm.contact.read"],
        },
        "timeout_seconds": 10,
        "audit": {
            "required": True,
            "events": ["requested", "authorized", "completed", "failed"],
            "record_input": False,
            "record_output": False,
        },
    }
    registered = client.post("/v1/capabilities", json={"contract": contract})
    assert registered.status_code == 201, registered.text
    return app, client


def invoke(client, key="invoke-1", input_value=None):
    return client.post(
        "/v1/capabilities/crm.contact.read/versions/1.0.0/invocations",
        headers={"Idempotency-Key": key},
        json={"input": input_value or {"contact_id": "contact-123"}},
    )


def test_capability_invocation_is_admitted_to_trusted_queue(tmp_path, monkeypatch):
    _, client = make_client(tmp_path, monkeypatch)
    response = invoke(client)
    assert response.status_code == 202, response.text
    job = response.json()
    assert job["state"] == "queued"
    assert job["tenant_id"] == "test-tenant"
    assert job["subject_id"] == "test-user"
    assert job["workload_kind"] == HANDLER["workload_kind"]
    assert job["handler_id"] == HANDLER["handler_id"]
    assert job["handler_version"] == HANDLER["handler_version"]
    assert job["workload_ref"] == "capability:crm.contact.read@1.0.0"
    assert job["created"] is True


def test_capability_invocation_is_idempotent_and_rejects_key_reuse(tmp_path, monkeypatch):
    _, client = make_client(tmp_path, monkeypatch)
    first = invoke(client, "same-key")
    replay = invoke(client, "same-key")
    conflict = invoke(client, "same-key", {"contact_id": "different"})
    assert first.status_code == 202
    assert replay.status_code == 202
    assert replay.json()["job_id"] == first.json()["job_id"]
    assert replay.json()["created"] is False
    assert conflict.status_code == 409


def test_capability_invocation_validates_input_and_rejects_client_handler(tmp_path, monkeypatch):
    _, client = make_client(tmp_path, monkeypatch)
    invalid = invoke(client, input_value={"unexpected": "value"})
    assert invalid.status_code == 422
    forged = client.post(
        "/v1/capabilities/crm.contact.read/versions/1.0.0/invocations",
        headers={"Idempotency-Key": "forged-handler"},
        json={"input": {"contact_id": "contact-123"}, "handler_id": "attacker.handler"},
    )
    assert forged.status_code == 422


def test_capability_invocation_fails_closed_without_binding_and_audits_denial(tmp_path, monkeypatch):
    app, client = make_client(tmp_path, monkeypatch, configured=False)
    unconfigured = invoke(client)
    assert unconfigured.status_code == 503

    app.state.capability_handler_bindings[("crm.contact.read", "1.0.0")] = tuple(HANDLER.values())
    app.state.authorization_policies = [
        policy for policy in app.state.authorization_policies
        if policy.get("policy_id") != "test.capability.invoke"
    ]
    denied = invoke(client, "denied")
    assert denied.status_code == 403
    events = app.state.authorization_audit.list_events()
    assert any(
        event["capability_id"] == "crm.contact.read"
        and event["action"] == "crm.contact.read"
        and event["allowed"] is False
        for event in events
    )

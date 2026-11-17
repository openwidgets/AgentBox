from __future__ import annotations

from pathlib import Path

from auth_helpers import configure_test_access
from fastapi.testclient import TestClient

from app.authentication import require_authenticated_principal
from app.main import create_app
from app.principal import Principal


def make_client(tmp_path: Path):
    app = configure_test_access(create_app(str(tmp_path / "capability-api.sqlite3")))
    return app, TestClient(app)


def payload(version="1.0.0"):
    return {
        "contract": {
            "schema_version": "1.0.0",
            "capability_id": "crm.contact.read",
            "version": version,
            "name": "Read contact",
            "description": "Read a tenant-owned contact record.",
            "input_schema": {"type": "object", "properties": {"contact_id": {"type": "string"}}, "required": ["contact_id"]},
            "output_schema": {"type": "object", "properties": {"name": {"type": "string"}}},
            "auth_ref": "policy:crm.contact.read",
            "permission_profile": {"principal_scope": "user", "actions": ["crm.contact.read"]},
            "timeout_seconds": 10,
            "audit": {"required": True, "events": ["requested", "authorized", "completed", "failed"], "record_input": False, "record_output": False},
        }
    }


def test_capability_api_registers_and_reads_immutable_contract(tmp_path):
    _, client = make_client(tmp_path)
    created = client.post("/v1/capabilities", json=payload())
    assert created.status_code == 201, created.text
    assert created.json()["created"] is True
    replay = client.post("/v1/capabilities", json=payload())
    assert replay.status_code == 201
    assert replay.json()["created"] is False
    fetched = client.get("/v1/capabilities/crm.contact.read/versions/1.0.0")
    assert fetched.status_code == 200
    assert fetched.json()["content_hash"] == created.json()["content_hash"]
    listed = client.get("/v1/capabilities")
    assert listed.status_code == 200
    assert len(listed.json()["items"]) == 1


def test_capability_api_rejects_mutation_and_invalid_contract(tmp_path):
    _, client = make_client(tmp_path)
    assert client.post("/v1/capabilities", json=payload()).status_code == 201
    changed = payload()
    changed["contract"]["description"] = "A different meaning."
    assert client.post("/v1/capabilities", json=changed).status_code == 409
    invalid = payload("2.0.0")
    invalid["contract"]["timeout_seconds"] = 0
    assert client.post("/v1/capabilities", json=invalid).status_code == 422


def test_capability_api_is_tenant_scoped(tmp_path):
    app, client = make_client(tmp_path)
    assert client.post("/v1/capabilities", json=payload()).status_code == 201
    app.state.tenants.create("tenant-other", "Other Tenant", "test")
    app.state.tenants.transition("tenant-other", "active", "test")
    app.dependency_overrides[require_authenticated_principal] = lambda: Principal(
        "other-user", "user", "tenant-other"
    )
    assert client.get("/v1/capabilities/crm.contact.read/versions/1.0.0").status_code == 404



def test_capability_api_denial_is_audited(tmp_path):
    app, client = make_client(tmp_path)
    app.state.authorization_policies = []
    response = client.get("/v1/capabilities")
    assert response.status_code == 403
    events = app.state.authorization_audit.list_events()
    assert events
    assert events[0]["capability_id"] == "capability.registry"
    assert events[0]["action"] == "capability.read"
    assert events[0]["allowed"] is False

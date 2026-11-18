from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime

import pytest
from auth_helpers import configure_test_access
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient

from app.enterprise_context import (
    ContextConflictError,
    ContextNotFoundError,
    ContextValidationError,
)
from app.main import create_app
from app.postgresql_connectors import PostgreSQLConnectorRegistry
from app.postgresql_provenance import PostgreSQLProvenanceVerifier


def public_key():
    key = Ed25519PrivateKey.generate().public_key()
    raw = key.public_bytes(Encoding.Raw, PublicFormat.Raw)
    return base64.b64encode(raw).decode("ascii"), raw


def test_connector_registration_is_tenant_scoped_and_key_is_persisted(tmp_path):
    path = tmp_path / "connectors.sqlite3"
    registry = PostgreSQLConnectorRegistry(path)
    encoded, raw = public_key()
    result = registry.register(
        "tenant-a", "warehouse-prod", "postgresql-catalog://warehouse/snapshot",
        "key-v1", encoded, "operator-a",
    )
    assert result["status"] == "active"
    assert registry.get_active_key(
        "tenant-a", "warehouse-prod", "key-v1",
        "postgresql-catalog://warehouse/snapshot",
    ) == raw
    with pytest.raises(ContextNotFoundError):
        registry.get_active_key(
            "tenant-b", "warehouse-prod", "key-v1",
            "postgresql-catalog://warehouse/snapshot",
        )
    with pytest.raises(ContextValidationError, match="source_ref does not match"):
        registry.get_active_key(
            "tenant-a", "warehouse-prod", "key-v1",
            "postgresql-catalog://other/snapshot",
        )
    registry.close()
    reopened = PostgreSQLConnectorRegistry(path)
    assert reopened.get_active_key(
        "tenant-a", "warehouse-prod", "key-v1",
        "postgresql-catalog://warehouse/snapshot",
    ) == raw


def test_key_rotation_keeps_old_key_until_explicit_revocation(tmp_path):
    registry = PostgreSQLConnectorRegistry(tmp_path / "rotation.sqlite3")
    first, first_raw = public_key()
    second, second_raw = public_key()
    registry.register(
        "tenant-a", "warehouse-prod", "postgresql-catalog://warehouse/snapshot",
        "key-v1", first, "operator-a",
    )
    registry.add_key("tenant-a", "warehouse-prod", "key-v2", second, "operator-b")
    assert registry.get_active_key(
        "tenant-a", "warehouse-prod", "key-v1",
        "postgresql-catalog://warehouse/snapshot",
    ) == first_raw
    assert registry.get_active_key(
        "tenant-a", "warehouse-prod", "key-v2",
        "postgresql-catalog://warehouse/snapshot",
    ) == second_raw
    registry.revoke_key("tenant-a", "warehouse-prod", "key-v1", "operator-a")
    with pytest.raises(ContextValidationError, match="unknown or revoked"):
        registry.get_active_key(
            "tenant-a", "warehouse-prod", "key-v1",
            "postgresql-catalog://warehouse/snapshot",
        )
    assert registry.get_active_key(
        "tenant-a", "warehouse-prod", "key-v2",
        "postgresql-catalog://warehouse/snapshot",
    ) == second_raw
    with pytest.raises(ContextConflictError, match="already revoked"):
        registry.revoke_key("tenant-a", "warehouse-prod", "key-v1", "operator-a")
    statuses = {row["key_id"]: row["key_status"] for row in registry.list_connectors("tenant-a")}
    assert statuses == {"key-v1": "revoked", "key-v2": "active"}
    assert [event["event_type"] for event in registry.events("tenant-a", "warehouse-prod")] == [
        "connector.registered", "connector.key_added", "connector.key_added",
        "connector.key_revoked",
    ]


def test_connector_registration_rejects_credentials_and_invalid_key_material(tmp_path):
    registry = PostgreSQLConnectorRegistry(tmp_path / "invalid.sqlite3")
    encoded, _ = public_key()
    with pytest.raises(ContextValidationError, match="normalized credential-free"):
        registry.register(
            "tenant-a", "warehouse-prod",
            "postgresql-catalog://user:secret@warehouse/snapshot",
            "key-v1", encoded, "operator-a",
        )
    with pytest.raises(ContextValidationError, match="exactly 32 bytes"):
        registry.register(
            "tenant-a", "warehouse-prod",
            "postgresql-catalog://warehouse/snapshot",
            "key-v1", base64.b64encode(b"short").decode(), "operator-a",
        )



def api_snapshot():
    snapshot = {
        "contract_version": "1.0.0",
        "source_kind": "postgresql_catalog",
        "discovered_at": datetime.now(UTC).isoformat(),
        "schemas": ["public"],
        "relations": [], "columns": [], "indexes": [], "constraints": [],
        "data_rows_read": 0, "writes_performed": 0,
    }
    snapshot["snapshot_hash"] = hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    return snapshot


def make_api_key():
    private = Ed25519PrivateKey.generate()
    raw_public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return private, base64.b64encode(raw_public).decode("ascii")


def test_api_registers_rotates_and_revokes_tenant_connector_keys(tmp_path):
    app = configure_test_access(create_app(str(tmp_path / "connector-api.sqlite3")))
    client = TestClient(app)
    source_ref = "postgresql-catalog://warehouse/snapshot"
    private_v1, public_v1 = make_api_key()
    registered = client.post(
        "/v1/enterprise-discovery/postgresql/connectors",
        json={
            "connector_id": "connector.alpha", "source_ref": source_ref,
            "key_id": "key-v1", "public_key": public_v1,
        },
    )
    assert registered.status_code == 201, registered.text
    _, public_v2 = make_api_key()
    rotated = client.post(
        "/v1/enterprise-discovery/postgresql/connectors/connector.alpha/keys",
        json={"key_id": "key-v2", "public_key": public_v2},
    )
    assert rotated.status_code == 201, rotated.text

    snapshot = api_snapshot()
    signed_at = datetime.now(UTC).isoformat()
    message = PostgreSQLProvenanceVerifier.signing_payload(
        tenant_id="test-tenant", source_ref=source_ref, snapshot=snapshot,
        key_id="key-v1", signed_at=signed_at,
    )
    attestation = {
        "algorithm": "Ed25519", "key_id": "key-v1", "signed_at": signed_at,
        "signature": base64.urlsafe_b64encode(private_v1.sign(message)).decode().rstrip("="),
    }
    accepted = client.post(
        "/v1/enterprise-discovery/postgresql/snapshots/ingest",
        json={
            "source_ref": source_ref, "connector_id": "connector.alpha",
            "snapshot": snapshot, "attestation": attestation,
        },
    )
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["provenance_status"] == "cryptographically_verified"

    revoked = client.post(
        "/v1/enterprise-discovery/postgresql/connectors/connector.alpha/keys/key-v1/revoke"
    )
    assert revoked.status_code == 200, revoked.text
    denied = client.post(
        "/v1/enterprise-discovery/postgresql/snapshots/ingest",
        json={
            "source_ref": source_ref, "connector_id": "connector.alpha",
            "snapshot": api_snapshot(), "attestation": attestation,
        },
    )
    assert denied.status_code == 422
    assert "unknown or revoked" in denied.text


def test_api_connector_management_requires_explicit_authorization(tmp_path):
    app = configure_test_access(create_app(str(tmp_path / "connector-deny.sqlite3")))
    app.state.authorization_policies = [
        policy for policy in app.state.authorization_policies
        if policy["actions"] != ["connector.register"]
    ]
    _, public_key_b64 = make_api_key()
    response = TestClient(app).post(
        "/v1/enterprise-discovery/postgresql/connectors",
        json={
            "connector_id": "connector.alpha",
            "source_ref": "postgresql-catalog://warehouse/snapshot",
            "key_id": "key-v1", "public_key": public_key_b64,
        },
    )
    assert response.status_code == 403



def test_connector_key_material_and_lifecycle_events_are_append_only(tmp_path):
    import sqlite3

    path = tmp_path / "append-only.sqlite3"
    registry = PostgreSQLConnectorRegistry(path)
    encoded, _ = public_key()
    registry.register(
        "tenant-a", "warehouse-prod", "postgresql-catalog://warehouse/snapshot",
        "key-v1", encoded, "operator-a",
    )
    with sqlite3.connect(path) as db:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            db.execute(
                "UPDATE pg_connector_keys SET public_key_b64=? WHERE key_id='key-v1'",
                ("A" * 44,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            db.execute(
                "DELETE FROM pg_connector_events WHERE event_type='connector.registered'"
            )

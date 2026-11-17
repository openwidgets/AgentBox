from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime

from auth_helpers import configure_test_access
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient

from app.main import create_app
from app.postgresql_provenance import PostgreSQLProvenanceVerifier


def catalog_snapshot():
    payload = {
        "contract_version": "1.0.0",
        "source_kind": "postgresql_catalog",
        "discovered_at": datetime.now(UTC).isoformat(),
        "schemas": ["public"],
        "relations": [
            {"schema_name": "public", "relation_name": "customers", "relation_kind": "table", "comment": None},
            {"schema_name": "public", "relation_name": "orders", "relation_kind": "table", "comment": None},
        ],
        "columns": [
            {"schema_name": "public", "relation_name": "customers", "ordinal_position": 1, "column_name": "id", "data_type": "bigint", "is_nullable": False, "comment": None},
            {"schema_name": "public", "relation_name": "orders", "ordinal_position": 2, "column_name": "customer_id", "data_type": "bigint", "is_nullable": False, "comment": None},
        ],
        "indexes": [
            {"schema_name": "public", "relation_name": "customers", "index_name": "customers_pkey", "is_primary": True, "is_unique": True, "definition": "CREATE UNIQUE INDEX customers_pkey"},
        ],
        "constraints": [
            {"schema_name": "public", "relation_name": "orders", "constraint_name": "orders_customer_fk", "constraint_kind": "foreign_key", "definition": "FOREIGN KEY (customer_id) REFERENCES customers(id)", "referenced_schema": "public", "referenced_relation": "customers"},
        ],
        "data_rows_read": 0,
        "writes_performed": 0,
    }
    payload["snapshot_hash"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()
    return payload


def test_catalog_snapshot_ingests_entities_relationships_and_unverified_evidence(tmp_path):
    app = configure_test_access(create_app(str(tmp_path / "discovery-ingest.sqlite3")))
    client = TestClient(app)
    response = client.post(
        "/v1/enterprise-discovery/postgresql/snapshots/ingest",
        json={
            "source_ref": "postgresql-catalog://warehouse-prod/snapshot",
            "snapshot": catalog_snapshot(),
        },
    )
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["entities"] == {"created": 7, "reused": 0}
    assert result["relationships"] == {"created": 7, "reused": 0}
    assert result["evidence"] == {"created": 7, "reused": 0}
    assert result["source_rows_read"] == 0
    assert result["source_writes_performed"] == 0
    assert result["validation_status"] == "unverified"

    relations = client.get("/v1/enterprise-context/entities?entity_type=database_relation").json()["items"]
    assert {item["name"] for item in relations} == {"customers", "orders"}
    edges = client.get("/v1/enterprise-context/relationships?limit=200").json()["items"]
    assert {item["relationship_type"] for item in edges} == {
        "contains_relation", "contains_column", "has_index", "has_constraint", "references_relation"
    }
    evidence = app.state.enterprise_context.query_evidence(
        relations[0]["entity_id"], "test-tenant"
    )
    assert evidence[0]["validation_status"] == "unverified"


def test_catalog_snapshot_ingestion_is_idempotent(tmp_path):
    app = configure_test_access(create_app(str(tmp_path / "discovery-repeat.sqlite3")))
    client = TestClient(app)
    request = {
        "source_ref": "postgresql-catalog://warehouse-prod/snapshot",
        "snapshot": catalog_snapshot(),
    }
    first = client.post("/v1/enterprise-discovery/postgresql/snapshots/ingest", json=request)
    second = client.post("/v1/enterprise-discovery/postgresql/snapshots/ingest", json=request)
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    result = second.json()
    assert result["entities"] == {"created": 0, "reused": 7}
    assert result["relationships"] == {"created": 0, "reused": 7}
    assert result["evidence"] == {"created": 0, "reused": 7}


def test_catalog_snapshot_ingestion_requires_explicit_discovery_grant(tmp_path):
    app = configure_test_access(create_app(str(tmp_path / "discovery-deny.sqlite3")))
    app.state.authorization_policies = [
        policy for policy in app.state.authorization_policies
        if policy["capability_id"] != "enterprise.discovery"
    ]
    response = TestClient(app).post(
        "/v1/enterprise-discovery/postgresql/snapshots/ingest",
        json={
            "source_ref": "postgresql-catalog://warehouse-prod/snapshot",
            "snapshot": catalog_snapshot(),
        },
    )
    assert response.status_code == 403


def test_catalog_snapshot_ingestion_rejects_tampering_and_unsafe_source_refs(tmp_path):
    app = configure_test_access(create_app(str(tmp_path / "discovery-invalid.sqlite3")))
    client = TestClient(app)
    tampered = catalog_snapshot()
    tampered["relations"][0]["relation_name"] = "changed"
    response = client.post(
        "/v1/enterprise-discovery/postgresql/snapshots/ingest",
        json={"source_ref": "postgresql-catalog://warehouse-prod/snapshot", "snapshot": tampered},
    )
    assert response.status_code == 422
    for source_ref in (
        "postgresql://user:password@db.example/catalog",
        "postgresql-catalog://warehouse-prod/snapshot?token=secret",
        "postgresql-catalog://warehouse-prod/snapshot#fragment",
    ):
        response = client.post(
            "/v1/enterprise-discovery/postgresql/snapshots/ingest",
            json={"source_ref": source_ref, "snapshot": catalog_snapshot()},
        )
        assert response.status_code == 422



def test_changed_catalog_metadata_creates_new_immutable_entity_version(tmp_path):
    app = configure_test_access(create_app(str(tmp_path / "discovery-version.sqlite3")))
    client = TestClient(app)
    source_ref = "postgresql-catalog://warehouse-prod/snapshot"
    first = catalog_snapshot()
    response = client.post(
        "/v1/enterprise-discovery/postgresql/snapshots/ingest",
        json={"source_ref": source_ref, "snapshot": first},
    )
    assert response.status_code == 201, response.text
    changed = catalog_snapshot()
    changed["relations"][0]["comment"] = "Customer master table"
    unsigned = dict(changed)
    unsigned.pop("snapshot_hash")
    changed["snapshot_hash"] = hashlib.sha256(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()
    response = client.post(
        "/v1/enterprise-discovery/postgresql/snapshots/ingest",
        json={"source_ref": source_ref, "snapshot": changed},
    )
    assert response.status_code == 201, response.text
    relation = next(
        item for item in client.get(
            "/v1/enterprise-context/entities?entity_type=database_relation"
        ).json()["items"] if item["name"] == "customers"
    )
    assert relation["version"] == 2
    assert relation["assertion_class"] == "inferred"
    evidence = app.state.enterprise_context.query_evidence(
        relation["entity_id"], "test-tenant"
    )
    assert any(item["entity_version"] == 2 for item in evidence)


def test_catalog_snapshot_ingestion_rejects_malformed_rows_and_oversized_payload(tmp_path):
    app = configure_test_access(create_app(str(tmp_path / "discovery-bounds.sqlite3")))
    client = TestClient(app)
    malformed = catalog_snapshot()
    malformed["relations"] = ["not-an-object"]
    unsigned = dict(malformed)
    unsigned.pop("snapshot_hash")
    malformed["snapshot_hash"] = hashlib.sha256(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()
    response = client.post(
        "/v1/enterprise-discovery/postgresql/snapshots/ingest",
        json={"source_ref": "postgresql-catalog://warehouse-prod/snapshot", "snapshot": malformed},
    )
    assert response.status_code == 422


def test_catalog_snapshot_ingestion_enforces_eight_mib_body_limit(tmp_path):
    app = configure_test_access(create_app(str(tmp_path / "discovery-size.sqlite3")))
    snapshot = catalog_snapshot()
    snapshot["relations"][0]["comment"] = "x" * (8 * 1024 * 1024 + 100)
    unsigned = dict(snapshot)
    unsigned.pop("snapshot_hash")
    snapshot["snapshot_hash"] = hashlib.sha256(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()
    response = TestClient(app).post(
        "/v1/enterprise-discovery/postgresql/snapshots/ingest",
        json={"source_ref": "postgresql-catalog://warehouse-prod/snapshot", "snapshot": snapshot},
    )
    assert response.status_code == 422



def make_connector_attestation(snapshot, source_ref, tenant_id="test-tenant"):
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    key_id = "warehouse-connector-v1"
    signed_at = datetime.now(UTC).isoformat()
    message = PostgreSQLProvenanceVerifier.signing_payload(
        tenant_id=tenant_id, source_ref=source_ref, snapshot=snapshot,
        key_id=key_id, signed_at=signed_at,
    )
    attestation = {
        "algorithm": "Ed25519",
        "key_id": key_id,
        "signed_at": signed_at,
        "signature": base64.urlsafe_b64encode(private.sign(message)).decode().rstrip("="),
    }
    return base64.b64encode(public).decode("ascii"), attestation


def test_signed_connector_snapshot_persists_verified_provenance_separately(tmp_path):
    app = configure_test_access(create_app(str(tmp_path / "discovery-signed.sqlite3")))
    client = TestClient(app)
    snapshot = catalog_snapshot()
    source_ref = "postgresql-catalog://warehouse-prod/snapshot"
    public_key_b64, attestation = make_connector_attestation(snapshot, source_ref)
    registered = client.post(
        "/v1/enterprise-discovery/postgresql/connectors",
        json={"connector_id": "warehouse-prod", "source_ref": source_ref,
              "key_id": "warehouse-connector-v1", "public_key": public_key_b64},
    )
    assert registered.status_code == 201, registered.text
    response = client.post(
        "/v1/enterprise-discovery/postgresql/snapshots/ingest",
        json={"source_ref": source_ref, "connector_id": "warehouse-prod",
              "snapshot": snapshot, "attestation": attestation},
    )
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["provenance_status"] == "cryptographically_verified"
    assert result["provenance_key_id"] == "warehouse-connector-v1"
    assert result["validation_status"] == "unverified"
    evidence = app.state.enterprise_context.query_evidence(
        app.state.enterprise_context.query_entities(
            "test-tenant", entity_type="database_relation", limit=1
        )[0]["entity_id"],
        "test-tenant",
    )
    assert evidence[0]["metadata"]["connector_attestation"]["key_id"] == "warehouse-connector-v1"


def test_signed_connector_snapshot_rejects_tampering_and_accepts_idempotent_replay(tmp_path):
    app = configure_test_access(create_app(str(tmp_path / "discovery-signed-invalid.sqlite3")))
    client = TestClient(app)
    snapshot = catalog_snapshot()
    source_ref = "postgresql-catalog://warehouse-prod/snapshot"
    public_key_b64, attestation = make_connector_attestation(snapshot, source_ref)
    registered = client.post(
        "/v1/enterprise-discovery/postgresql/connectors",
        json={"connector_id": "warehouse-prod", "source_ref": source_ref,
              "key_id": "warehouse-connector-v1", "public_key": public_key_b64},
    )
    assert registered.status_code == 201, registered.text
    tampered = dict(snapshot)
    tampered["schemas"] = ["other"]
    tampered["snapshot_hash"] = "b" * 64
    response = client.post(
        "/v1/enterprise-discovery/postgresql/snapshots/ingest",
        json={"source_ref": source_ref, "connector_id": "warehouse-prod",
              "snapshot": tampered, "attestation": attestation},
    )
    assert response.status_code == 422
    assert "signature verification failed" in response.text
    replay = client.post(
        "/v1/enterprise-discovery/postgresql/snapshots/ingest",
        json={
            "source_ref": source_ref, "connector_id": "warehouse-prod",
            "snapshot": snapshot, "attestation": attestation,
        },
    )
    assert replay.status_code == 201, replay.text


def test_signed_connector_snapshot_fails_closed_when_key_is_not_trusted(tmp_path):
    app = configure_test_access(create_app(str(tmp_path / "discovery-no-trust.sqlite3")))
    client = TestClient(app)
    snapshot = catalog_snapshot()
    source_ref = "postgresql-catalog://warehouse-prod/snapshot"
    public_key_b64, attestation = make_connector_attestation(snapshot, source_ref)
    registered = client.post(
        "/v1/enterprise-discovery/postgresql/connectors",
        json={"connector_id": "warehouse-prod", "source_ref": source_ref,
              "key_id": "registered-key", "public_key": public_key_b64},
    )
    assert registered.status_code == 201, registered.text
    response = client.post(
        "/v1/enterprise-discovery/postgresql/snapshots/ingest",
        json={"source_ref": source_ref, "connector_id": "warehouse-prod",
              "snapshot": snapshot, "attestation": attestation},
    )
    assert response.status_code == 422
    assert "unknown or revoked" in response.text

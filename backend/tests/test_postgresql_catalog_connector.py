from __future__ import annotations

import base64
from datetime import UTC, datetime

import pytest
from auth_helpers import configure_test_access
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient

from app.enterprise_context import ContextValidationError
from app.main import create_app
from app.postgresql_catalog_connector import (
    PostgreSQLCatalogAttestor,
    PostgreSQLCatalogConnector,
    PostgreSQLCatalogReader,
)
from app.postgresql_provenance import PostgreSQLProvenanceVerifier


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.result = []

    def execute(self, query, params=None):
        self.connection.statements.append((query, params))
        if "FROM pg_catalog.pg_namespace AS n" in query:
            self.result = [("public",)]
        elif "FROM pg_catalog.pg_attribute AS a" in query:
            self.result = [("public", "customers", 1, "id", "bigint", True, None)]
        elif "FROM pg_catalog.pg_index AS x" in query:
            self.result = [
                ("public", "customers", "customers_pkey", True, True,
                 "CREATE UNIQUE INDEX customers_pkey")
            ]
        elif "FROM pg_catalog.pg_constraint AS con" in query:
            self.result = [
                ("public", "customers", "customers_pkey", "primary_key",
                 "PRIMARY KEY (id)", None, None)
            ]
        elif "FROM pg_catalog.pg_class AS c" in query:
            self.result = [("public", "customers", "table", "customer master")]
        else:
            self.result = []

    def fetchall(self):
        return self.result

    def close(self):
        self.connection.cursor_closed = True


class FakeConnection:
    def __init__(self):
        self.statements = []
        self.cursor_closed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class FakeSigningProvider:
    def __init__(self):
        self.private = Ed25519PrivateKey.generate()
        self.messages = []

    def sign(self, key_id, message):
        self.messages.append((key_id, message))
        return self.private.sign(message)

    def public_bytes(self):
        return self.private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def test_catalog_reader_collects_metadata_in_read_only_transaction_and_closes_connection():
    connection = FakeConnection()
    snapshot = PostgreSQLCatalogReader().collect(lambda: connection, ["public"])
    assert snapshot["source_kind"] == "postgresql_catalog"
    assert snapshot["schemas"] == ["public"]
    assert snapshot["relations"][0]["relation_name"] == "customers"
    assert snapshot["columns"][0]["column_name"] == "id"
    assert snapshot["indexes"][0]["is_primary"] is True
    assert snapshot["constraints"][0]["constraint_kind"] == "primary_key"
    assert snapshot["data_rows_read"] == 0
    assert snapshot["writes_performed"] == 0
    assert len(snapshot["snapshot_hash"]) == 64
    sql = [statement for statement, _ in connection.statements]
    assert sql[0] == "BEGIN TRANSACTION READ ONLY"
    assert "set_config" in sql[1]
    assert all(
        "SELECT" in statement or "BEGIN TRANSACTION READ ONLY" in statement
        for statement in sql
    )
    assert all(
        not any(keyword in statement for keyword in ("INSERT ", "UPDATE ", "DELETE "))
        for statement in sql
    )
    assert connection.cursor_closed and connection.rolled_back and connection.closed


def test_connector_attestation_matches_agentbox_verifier_contract():
    provider = FakeSigningProvider()
    reader = PostgreSQLCatalogReader()
    connector = PostgreSQLCatalogConnector(reader, PostgreSQLCatalogAttestor(provider))
    connection = FakeConnection()
    result = connector.discover(
        connection_factory=lambda: connection,
        schema_names=["public"],
        tenant_id="tenant-a",
        source_ref="postgresql-catalog://warehouse/snapshot",
        connector_id="warehouse-prod",
        key_id="key-v1",
    )
    assert result["connector_id"] == "warehouse-prod"
    assert result["attestation"]["algorithm"] == "Ed25519"
    verifier = PostgreSQLProvenanceVerifier({"key-v1": provider.public_bytes()})
    verified = verifier.verify(
        result["attestation"], result["snapshot"], "tenant-a",
        "postgresql-catalog://warehouse/snapshot",
    )
    assert verified["verified_snapshot_hash"] == result["snapshot"]["snapshot_hash"]
    assert provider.messages[0][0] == "key-v1"


@pytest.mark.parametrize("schemas", [[], ["public", "public"], ["x" * 64], [""]])
def test_catalog_reader_rejects_invalid_schema_allowlists(schemas):
    with pytest.raises(ContextValidationError, match="schema_names"):
        PostgreSQLCatalogReader().collect(lambda: FakeConnection(), schemas)


def test_catalog_reader_fails_closed_when_no_requested_schema_is_visible():
    class EmptyCursor(FakeCursor):
        def execute(self, query, params=None):
            self.connection.statements.append((query, params))
            self.result = []

    class EmptyConnection(FakeConnection):
        def cursor(self):
            return EmptyCursor(self)

    connection = EmptyConnection()
    with pytest.raises(ContextValidationError, match="missing or not visible"):
        PostgreSQLCatalogReader().collect(lambda: connection, ["private"])
    assert connection.rolled_back and connection.closed


def test_catalog_reader_closes_connection_after_database_error():
    class FailingCursor(FakeCursor):
        def execute(self, query, params=None):
            self.connection.statements.append((query, params))
            if query == "BEGIN TRANSACTION READ ONLY":
                raise RuntimeError("connection rejected transaction")
            self.result = []

    class FailingConnection(FakeConnection):
        def cursor(self):
            return FailingCursor(self)

    connection = FailingConnection()
    with pytest.raises(ContextValidationError, match="collection failed"):
        PostgreSQLCatalogReader().collect(lambda: connection, ["public"])
    assert connection.cursor_closed and connection.rolled_back and connection.closed


def test_attestor_rejects_invalid_signing_provider_output():
    class InvalidProvider:
        def sign(self, key_id, message):
            return b"not-a-signature"

    snapshot = {
        "snapshot_hash": "a" * 64,
        "discovered_at": datetime.now(UTC).isoformat(),
    }
    with pytest.raises(ContextValidationError, match="64-byte"):
        PostgreSQLCatalogAttestor(InvalidProvider()).attest(
            tenant_id="tenant-a", source_ref="postgresql-catalog://warehouse/snapshot",
            snapshot=snapshot, key_id="key-v1",
        )



def test_fake_source_connector_round_trips_signed_snapshot_through_api(tmp_path):
    provider = FakeSigningProvider()
    connector = PostgreSQLCatalogConnector(
        PostgreSQLCatalogReader(), PostgreSQLCatalogAttestor(provider)
    )
    source_ref = "postgresql-catalog://warehouse/snapshot"
    envelope = connector.discover(
        connection_factory=FakeConnection,
        schema_names=["public"],
        tenant_id="test-tenant",
        source_ref=source_ref,
        connector_id="warehouse-prod",
        key_id="key-v1",
    )
    app = configure_test_access(create_app(str(tmp_path / "connector-e2e.sqlite3")))
    client = TestClient(app)
    public_key = base64.b64encode(provider.public_bytes()).decode("ascii")
    registered = client.post(
        "/v1/enterprise-discovery/postgresql/connectors",
        json={
            "connector_id": "warehouse-prod", "source_ref": source_ref,
            "key_id": "key-v1", "public_key": public_key,
        },
    )
    assert registered.status_code == 201, registered.text
    response = client.post(
        "/v1/enterprise-discovery/postgresql/snapshots/ingest",
        json=envelope,
    )
    assert response.status_code == 201, response.text
    assert response.json()["provenance_status"] == "cryptographically_verified"
    assert response.json()["validation_status"] == "unverified"



def test_catalog_reader_rejects_partially_visible_schema_allowlist():
    class PartialCursor(FakeCursor):
        def execute(self, query, params=None):
            super().execute(query, params)
            if "FROM pg_catalog.pg_namespace AS n" in query:
                self.result = [("public",)]

    class PartialConnection(FakeConnection):
        def cursor(self):
            return PartialCursor(self)

    with pytest.raises(ContextValidationError, match="missing or not visible"):
        PostgreSQLCatalogReader().collect(
            lambda: PartialConnection(), ["public", "private"]
        )


@pytest.mark.parametrize(
    "source_ref",
    [
        "postgresql-catalog://user:secret@warehouse/snapshot",
        "postgresql-catalog://warehouse/snapshot?password=secret",
        "postgresql-catalog://warehouse/snapshot/",
        "https://warehouse/snapshot",
    ],
)
def test_attestor_rejects_unsafe_source_locators(source_ref):
    provider = FakeSigningProvider()
    snapshot = {
        "snapshot_hash": "a" * 64,
        "discovered_at": datetime.now(UTC).isoformat(),
    }
    with pytest.raises(ContextValidationError, match="normalized credential-free"):
        PostgreSQLCatalogAttestor(provider).attest(
            tenant_id="tenant-a", source_ref=source_ref,
            snapshot=snapshot, key_id="key-v1",
        )



def test_attestor_rejects_snapshot_without_valid_digest():
    provider = FakeSigningProvider()
    with pytest.raises(ContextValidationError, match="SHA-256 digest"):
        PostgreSQLCatalogAttestor(provider).attest(
            tenant_id="tenant-a",
            source_ref="postgresql-catalog://warehouse/snapshot",
            snapshot={"snapshot_hash": "not-a-digest", "discovered_at": "now"},
            key_id="key-v1",
        )


def test_catalog_reader_bounds_rows_at_database_query_and_fails_on_overflow(monkeypatch):
    import app.postgresql_catalog_connector as connector_module

    monkeypatch.setattr(connector_module, "MAX_RELATIONS", 1)

    class OverflowCursor(FakeCursor):
        def execute(self, query, params=None):
            super().execute(query, params)
            if "FROM pg_catalog.pg_class AS c" in query:
                self.result = [
                    ("public", "customers", "table", None),
                    ("public", "orders", "table", None),
                ]

    class OverflowConnection(FakeConnection):
        def cursor(self):
            return OverflowCursor(self)

    connection = OverflowConnection()
    with pytest.raises(ContextValidationError, match="exceeds 1 relations"):
        PostgreSQLCatalogReader().collect(lambda: connection, ["public"])

    relation_queries = [
        statement for statement, _ in connection.statements
        if "FROM pg_catalog.pg_class AS c" in statement
    ]
    assert len(relation_queries) == 1
    assert relation_queries[0].rstrip().endswith("LIMIT 2")
    assert connection.cursor_closed and connection.rolled_back and connection.closed


def test_catalog_reader_adds_server_side_limit_to_each_catalog_query():
    connection = FakeConnection()
    PostgreSQLCatalogReader().collect(lambda: connection, ["public"])

    catalog_queries = [
        statement for statement, _ in connection.statements
        if "FROM pg_catalog." in statement
    ]
    assert len(catalog_queries) == 5
    assert all("LIMIT " in statement for statement in catalog_queries)


@pytest.mark.parametrize("timeout", [True, False, 99, 120001, 1.5, "15000"])
def test_catalog_reader_rejects_invalid_statement_timeout(timeout):
    with pytest.raises(ValueError, match="statement_timeout_ms"):
        PostgreSQLCatalogReader(statement_timeout_ms=timeout)

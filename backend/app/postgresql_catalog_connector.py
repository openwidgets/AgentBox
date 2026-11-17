"""Read-only PostgreSQL catalog collection and connector-side attestation.

This module is intended to run beside the source database. It accepts an injected
DB-API connection factory and signing provider; AgentBox never receives a DSN,
database password, or private signing key.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Sequence
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import urlsplit

from .enterprise_context import ContextValidationError

MAX_SCHEMAS = 100
MAX_RELATIONS = 2000
MAX_COLUMNS = 20000
MAX_INDEXES = 10000
MAX_CONSTRAINTS = 10000
MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024
MAX_INGEST_ENTITIES = 5000
SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")


class SigningKeyProvider(Protocol):
    """Adapter contract for KMS, HSM or OS keyring-backed signing."""

    def sign(self, key_id: str, message: bytes) -> bytes:
        """Return a raw Ed25519 signature without exporting private key material."""
        ...


class PostgreSQLCatalogReader:
    """Collect schema metadata only through a transaction forced read-only."""

    SCHEMAS_SQL = """
        SELECT n.nspname
        FROM pg_catalog.pg_namespace AS n
        WHERE n.nspname = ANY(%s)
          AND n.nspname NOT IN ('pg_catalog', 'information_schema')
        ORDER BY n.nspname
    """
    RELATIONS_SQL = """
        SELECT n.nspname, c.relname,
               CASE c.relkind
                 WHEN 'r' THEN 'table' WHEN 'p' THEN 'partitioned_table'
                 WHEN 'v' THEN 'view' WHEN 'm' THEN 'materialized_view'
                 WHEN 'f' THEN 'foreign_table' ELSE c.relkind::text
               END,
               pg_catalog.obj_description(c.oid, 'pg_class')
        FROM pg_catalog.pg_class AS c
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname = ANY(%s)
          AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
        ORDER BY n.nspname, c.relname
    """
    COLUMNS_SQL = """
        SELECT n.nspname, c.relname, a.attnum, a.attname,
               pg_catalog.format_type(a.atttypid, a.atttypmod),
               NOT a.attnotnull,
               pg_catalog.col_description(c.oid, a.attnum)
        FROM pg_catalog.pg_attribute AS a
        JOIN pg_catalog.pg_class AS c ON c.oid = a.attrelid
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname = ANY(%s)
          AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
          AND a.attnum > 0 AND NOT a.attisdropped
        ORDER BY n.nspname, c.relname, a.attnum
    """
    INDEXES_SQL = """
        SELECT n.nspname, t.relname, i.relname,
               x.indisprimary, x.indisunique,
               pg_catalog.pg_get_indexdef(i.oid)
        FROM pg_catalog.pg_index AS x
        JOIN pg_catalog.pg_class AS t ON t.oid = x.indrelid
        JOIN pg_catalog.pg_class AS i ON i.oid = x.indexrelid
        JOIN pg_catalog.pg_namespace AS n ON n.oid = t.relnamespace
        WHERE n.nspname = ANY(%s)
        ORDER BY n.nspname, t.relname, i.relname
    """
    CONSTRAINTS_SQL = """
        SELECT n.nspname, t.relname, con.conname,
               CASE con.contype
                 WHEN 'p' THEN 'primary_key' WHEN 'f' THEN 'foreign_key'
                 WHEN 'u' THEN 'unique' WHEN 'c' THEN 'check'
                 WHEN 'x' THEN 'exclusion' ELSE con.contype::text
               END,
               pg_catalog.pg_get_constraintdef(con.oid, true),
               rn.nspname, rt.relname
        FROM pg_catalog.pg_constraint AS con
        JOIN pg_catalog.pg_class AS t ON t.oid = con.conrelid
        JOIN pg_catalog.pg_namespace AS n ON n.oid = t.relnamespace
        LEFT JOIN pg_catalog.pg_class AS rt ON rt.oid = con.confrelid
        LEFT JOIN pg_catalog.pg_namespace AS rn ON rn.oid = rt.relnamespace
        WHERE n.nspname = ANY(%s)
        ORDER BY n.nspname, t.relname, con.conname
    """

    def __init__(self, *, statement_timeout_ms: int = 15000):
        if type(statement_timeout_ms) is not int or not 100 <= statement_timeout_ms <= 120000:
            raise ValueError("statement_timeout_ms must be between 100 and 120000")
        self.statement_timeout_ms = statement_timeout_ms

    @staticmethod
    def _rows(
        cursor,
        query: str,
        schemas: Sequence[str],
        *,
        maximum: int,
        label: str,
    ) -> list[tuple[Any, ...]]:
        # Fetch at most maximum + 1 rows; the extra row is an overflow sentinel.
        cursor.execute(f"{query.rstrip()} LIMIT {maximum + 1}", (list(schemas),))
        rows = list(cursor.fetchall())
        if len(rows) > maximum:
            raise ContextValidationError(f"Catalog snapshot exceeds {maximum} {label}")
        return rows

    def collect(
        self,
        connection_factory,
        schema_names: Sequence[str],
    ) -> dict[str, Any]:
        if not callable(connection_factory):
            raise ContextValidationError("connection_factory must be callable")
        if (
            not isinstance(schema_names, (list, tuple))
            or not schema_names or len(schema_names) > MAX_SCHEMAS
            or any(not isinstance(name, str) or not name or len(name) > 63 for name in schema_names)
            or len(set(schema_names)) != len(schema_names)
        ):
            raise ContextValidationError(
                "schema_names must contain 1-100 unique PostgreSQL schema identifiers"
            )
        connection = None
        cursor = None
        try:
            connection = connection_factory()
            if connection is None or not callable(getattr(connection, "cursor", None)):
                raise ContextValidationError(
                    "connection_factory did not return a DB-API connection"
                )
            cursor = connection.cursor()
            cursor.execute("BEGIN TRANSACTION READ ONLY")
            cursor.execute(
                "SELECT pg_catalog.set_config('statement_timeout', %s, true)",
                (f"{self.statement_timeout_ms}ms",),
            )
            schemas = [
                row[0] for row in self._rows(
                    cursor, self.SCHEMAS_SQL, schema_names,
                    maximum=MAX_SCHEMAS, label="schemas",
                )
            ]
            if set(schemas) != set(schema_names):
                raise ContextValidationError(
                    "One or more requested PostgreSQL schemas are missing or not visible"
                )
            relations = [
                {"schema_name": r[0], "relation_name": r[1], "relation_kind": r[2], "comment": r[3]}
                for r in self._rows(
                    cursor, self.RELATIONS_SQL, schemas,
                    maximum=MAX_RELATIONS, label="relations",
                )
            ]
            columns = [
                {"schema_name": r[0], "relation_name": r[1], "ordinal_position": r[2],
                 "column_name": r[3], "data_type": r[4], "is_nullable": r[5], "comment": r[6]}
                for r in self._rows(
                    cursor, self.COLUMNS_SQL, schemas,
                    maximum=MAX_COLUMNS, label="columns",
                )
            ]
            indexes = [
                {"schema_name": r[0], "relation_name": r[1], "index_name": r[2],
                 "is_primary": r[3], "is_unique": r[4], "definition": r[5]}
                for r in self._rows(
                    cursor, self.INDEXES_SQL, schemas,
                    maximum=MAX_INDEXES, label="indexes",
                )
            ]
            constraints = [
                {"schema_name": r[0], "relation_name": r[1], "constraint_name": r[2],
                 "constraint_kind": r[3], "definition": r[4],
                 "referenced_schema": r[5], "referenced_relation": r[6]}
                for r in self._rows(
                    cursor, self.CONSTRAINTS_SQL, schemas,
                    maximum=MAX_CONSTRAINTS, label="constraints",
                )
            ]
            limits = (
                (len(schemas), MAX_SCHEMAS, "schemas"),
                (len(relations), MAX_RELATIONS, "relations"),
                (len(columns), MAX_COLUMNS, "columns"),
                (len(indexes), MAX_INDEXES, "indexes"),
                (len(constraints), MAX_CONSTRAINTS, "constraints"),
            )
            for actual, maximum, label in limits:
                if actual > maximum:
                    raise ContextValidationError(f"Catalog snapshot exceeds {maximum} {label}")
            if sum(item[0] for item in limits) > MAX_INGEST_ENTITIES:
                raise ContextValidationError(
                    "Catalog snapshot exceeds the "
                    f"{MAX_INGEST_ENTITIES} total entity ingestion limit"
                )
            snapshot = {
                "contract_version": "1.0.0",
                "source_kind": "postgresql_catalog",
                "discovered_at": datetime.now(UTC).isoformat(),
                "schemas": schemas,
                "relations": relations,
                "columns": columns,
                "indexes": indexes,
                "constraints": constraints,
                "data_rows_read": 0,
                "writes_performed": 0,
            }
            snapshot["snapshot_hash"] = hashlib.sha256(_canonical(snapshot)).hexdigest()
            if len(_canonical(snapshot)) > MAX_SNAPSHOT_BYTES:
                raise ContextValidationError("Catalog snapshot exceeds the 8 MiB connector limit")
            return snapshot
        except ContextValidationError:
            raise
        except Exception as exc:  # noqa: BLE001 - redact arbitrary driver exception details
            raise ContextValidationError("Read-only PostgreSQL catalog collection failed") from exc
        finally:
            if cursor is not None:
                with suppress(Exception):
                    cursor.close()
            if connection is not None:
                with suppress(Exception):
                    connection.rollback()
                with suppress(Exception):
                    connection.close()


class PostgreSQLCatalogAttestor:
    """Create the exact API attestation using a non-exportable signing provider."""

    def __init__(self, signing_provider: SigningKeyProvider):
        if not callable(getattr(signing_provider, "sign", None)):
            raise ValueError("signing_provider must implement sign(key_id, message)")
        self.signing_provider = signing_provider

    @staticmethod
    def signing_payload(
        *, tenant_id: str, source_ref: str, snapshot: dict[str, Any],
        key_id: str, signed_at: str,
    ) -> bytes:
        return _canonical({
            "contract": "agentbox.postgresql-catalog-attestation.v1",
            "tenant_id": tenant_id,
            "source_ref": source_ref,
            "snapshot_hash": snapshot.get("snapshot_hash"),
            "discovered_at": snapshot.get("discovered_at"),
            "key_id": key_id,
            "signed_at": signed_at,
        })

    def attest(
        self, *, tenant_id: str, source_ref: str, snapshot: dict[str, Any],
        key_id: str,
    ) -> dict[str, str]:
        if not isinstance(tenant_id, str) or not tenant_id:
            raise ContextValidationError("tenant_id is required for connector attestation")
        try:
            locator = urlsplit(source_ref)
        except (TypeError, ValueError) as exc:
            raise ContextValidationError(
                "source_ref must be a valid PostgreSQL catalog locator"
            ) from exc
        if (
            not isinstance(source_ref, str) or not 1 <= len(source_ref) <= 1024
            or locator.scheme != "postgresql-catalog" or not locator.netloc
            or locator.username or locator.password or locator.query or locator.fragment
            or source_ref != source_ref.rstrip("/")
        ):
            raise ContextValidationError(
                "source_ref must be a normalized credential-free PostgreSQL catalog locator"
            )
        if not isinstance(key_id, str) or not key_id or len(key_id) > 128:
            raise ContextValidationError("key_id is required and must not exceed 128 characters")
        if (
            not isinstance(snapshot, dict)
            or not isinstance(snapshot.get("snapshot_hash"), str)
            or not SHA256_PATTERN.fullmatch(snapshot["snapshot_hash"])
            or not isinstance(snapshot.get("discovered_at"), str)
        ):
            raise ContextValidationError(
                "snapshot must include a SHA-256 digest and discovery timestamp"
            )
        signed_at = datetime.now(UTC).isoformat()
        message = self.signing_payload(
            tenant_id=tenant_id, source_ref=source_ref, snapshot=snapshot,
            key_id=key_id, signed_at=signed_at,
        )
        signature = self.signing_provider.sign(key_id, message)
        if not isinstance(signature, bytes) or len(signature) != 64:
            raise ContextValidationError("signing provider must return a 64-byte Ed25519 signature")
        return {
            "algorithm": "Ed25519",
            "key_id": key_id,
            "signed_at": signed_at,
            "signature": base64.urlsafe_b64encode(signature).decode("ascii").rstrip("="),
        }


class PostgreSQLCatalogConnector:
    """Collect a metadata-only snapshot and sign it for the AgentBox ingestion API."""

    def __init__(self, reader: PostgreSQLCatalogReader, attestor: PostgreSQLCatalogAttestor):
        self.reader = reader
        self.attestor = attestor

    def discover(
        self, *, connection_factory, schema_names: Sequence[str], tenant_id: str,
        source_ref: str, connector_id: str, key_id: str,
    ) -> dict[str, Any]:
        snapshot = self.reader.collect(connection_factory, schema_names)
        if not isinstance(connector_id, str) or not connector_id:
            raise ContextValidationError("connector_id is required")
        attestation = self.attestor.attest(
            tenant_id=tenant_id, source_ref=source_ref,
            snapshot=snapshot, key_id=key_id,
        )
        return {
            "connector_id": connector_id,
            "source_ref": source_ref,
            "snapshot": snapshot,
            "attestation": attestation,
        }

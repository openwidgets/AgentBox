"""Tenant-scoped immutable capability contract registry.

This registry stores declarative contracts only. It never imports or executes an
implementation_ref, resolves credentials, or grants invocation authority.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .capability_contract import validate_capability

_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{2,127}$")
_VERSION = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")


class CapabilityRegistryError(Exception):
    """Base registry error."""


class CapabilityValidationError(CapabilityRegistryError):
    """Capability contract is invalid."""


class CapabilityConflictError(CapabilityRegistryError):
    """An immutable capability version already exists with different content."""


class CapabilityNotFoundError(CapabilityRegistryError):
    """Capability version does not exist in the requesting tenant."""


@dataclass(frozen=True)
class CapabilityRecord:
    tenant_id: str
    capability_id: str
    version: str
    content_hash: str
    contract: dict[str, Any]
    created_at: str


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


class CapabilityRegistry:
    """Persist immutable, tenant-isolated capability contract versions."""

    def __init__(self, database_path: str | Path):
        self.database_path = str(database_path)
        self._closed = False
        if self.database_path != ":memory:":
            Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self._memory = self.database_path == ":memory:"
        self._dsn = (
            f"file:agentbox-capabilities-{id(self)}?mode=memory&cache=shared"
            if self._memory else self.database_path
        )
        self._keeper = sqlite3.connect(self._dsn, uri=True, timeout=10) if self._memory else None
        self._initialize()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._keeper is not None:
            self._keeper.close()
            self._keeper = None

    def _connect(self) -> sqlite3.Connection:
        if self._closed:
            raise RuntimeError("Capability registry is closed")
        connection = sqlite3.connect(self._dsn, uri=self._memory, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            if not self._memory:
                connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS capability_versions (
                    tenant_id TEXT NOT NULL,
                    capability_id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    contract_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, capability_id, version),
                    FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id)
                )
            """)
            connection.execute("""
                CREATE INDEX IF NOT EXISTS idx_capability_versions_tenant
                ON capability_versions(tenant_id, capability_id, created_at)
            """)
            connection.execute("""
                CREATE TRIGGER IF NOT EXISTS capability_versions_no_update
                BEFORE UPDATE ON capability_versions
                BEGIN SELECT RAISE(ABORT, 'capability versions are immutable'); END
            """)
            connection.execute("""
                CREATE TRIGGER IF NOT EXISTS capability_versions_no_delete
                BEFORE DELETE ON capability_versions
                BEGIN SELECT RAISE(ABORT, 'capability versions are immutable'); END
            """)

    @staticmethod
    def _record(row: sqlite3.Row) -> CapabilityRecord:
        return CapabilityRecord(
            tenant_id=row["tenant_id"],
            capability_id=row["capability_id"],
            version=row["version"],
            content_hash=row["content_hash"],
            contract=json.loads(row["contract_json"]),
            created_at=row["created_at"],
        )

    def register(self, contract: dict[str, Any], tenant_id: str) -> tuple[CapabilityRecord, bool]:
        if not isinstance(tenant_id, str) or not tenant_id.strip() or len(tenant_id) > 128:
            raise CapabilityValidationError("A valid tenant identity is required")
        if not isinstance(contract, dict):
            raise CapabilityValidationError("Capability contract must be an object")
        errors = validate_capability(contract)
        if errors:
            raise CapabilityValidationError(json.dumps(errors, sort_keys=True))
        capability_id, version = contract["capability_id"], contract["version"]
        if not _ID.fullmatch(capability_id) or not _VERSION.fullmatch(version):
            raise CapabilityValidationError("Capability ID or semantic version is invalid")
        serialized = _canonical(contract)
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """SELECT * FROM capability_versions
                WHERE tenant_id = ? AND capability_id = ? AND version = ?""",
                (tenant_id, capability_id, version),
            ).fetchone()
            if existing is not None:
                record = self._record(existing)
                if record.content_hash != digest:
                    raise CapabilityConflictError(
                        f"{capability_id}@{version} is immutable and already exists"
                    )
                return record, False
            created_at = datetime.now(UTC).isoformat()
            connection.execute(
                """INSERT INTO capability_versions
                (tenant_id, capability_id, version, content_hash, contract_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (tenant_id, capability_id, version, digest, serialized, created_at),
            )
            row = connection.execute(
                """SELECT * FROM capability_versions
                WHERE tenant_id = ? AND capability_id = ? AND version = ?""",
                (tenant_id, capability_id, version),
            ).fetchone()
        return self._record(row), True

    def get(self, capability_id: str, version: str, tenant_id: str) -> CapabilityRecord:
        with self._connection() as connection:
            row = connection.execute(
                """SELECT * FROM capability_versions
                WHERE tenant_id = ? AND capability_id = ? AND version = ?""",
                (tenant_id, capability_id, version),
            ).fetchone()
        if row is None:
            raise CapabilityNotFoundError(f"{capability_id}@{version} was not found")
        record = self._record(row)
        if _digest(record.contract) != record.content_hash:
            raise CapabilityRegistryError("Stored capability content hash mismatch")
        return record

    def list(self, tenant_id: str, limit: int = 50, offset: int = 0) -> list[CapabilityRecord]:
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("limit must be 1..100 and offset must be non-negative")
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT * FROM capability_versions WHERE tenant_id = ?
                ORDER BY capability_id, created_at DESC, version DESC LIMIT ? OFFSET ?""",
                (tenant_id, limit, offset),
            ).fetchall()
        records = [self._record(row) for row in rows]
        for record in records:
            if _digest(record.contract) != record.content_hash:
                raise CapabilityRegistryError(
                    f"Stored capability content hash mismatch: "
                    f"{record.capability_id}@{record.version}"
                )
        return records

"""Authenticated, encrypted, tenant-scoped logical backup and restore.

Backup encryption keys are supplied by the deployment secret manager through
environment variables. AgentBox never generates, stores, or logs key material.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import sqlite3
import stat
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .tenant import validate_tenant_id

BACKUP_FORMAT = "agentbox-tenant-backup"
BACKUP_VERSION = 1
ALGORITHM = "AES-256-GCM"
KEY_ID_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9_]{0,63}$")
TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "agent_versions": (
        "tenant_id", "agent_id", "version", "content_hash", "manifest_json",
        "parent_artifact_id", "created_at",
    ),
    "evaluation_profiles": (
        "tenant_id", "profile_id", "version", "content_hash", "profile_json", "created_at",
    ),
    "executions": (
        "execution_id", "agent_id", "agent_version", "idempotency_key", "request_hash",
        "status", "result_json", "error_code", "created_at", "completed_at", "tenant_id",
    ),
    "evaluation_runs": (
        "run_id", "tenant_id", "agent_id", "agent_version", "profile_id", "profile_version",
        "status", "score", "passed", "result_json", "created_at",
    ),
    "tenant_audit": (
        "tenant_id", "action", "previous_status", "new_status", "actor_id", "occurred_at",
    ),
    "context_entities": (
        "tenant_id", "entity_id", "version", "entity_type", "name", "description",
        "attributes_json", "assertion_class", "confidence", "valid_from", "valid_to",
        "content_hash", "created_at", "created_by",
    ),
    "context_relationships": (
        "tenant_id", "relationship_id", "version", "relationship_type", "source_entity_id",
        "source_version", "target_entity_id", "target_version", "attributes_json",
        "assertion_class", "confidence", "valid_from", "valid_to", "content_hash",
        "created_at", "created_by",
    ),
    "context_evidence": (
        "tenant_id", "evidence_id", "entity_id", "entity_version", "relationship_id",
        "relationship_version", "source_kind", "source_ref", "source_hash", "observed_at",
        "confidence", "validation_status", "metadata_json", "recorded_at", "recorded_by",
    ),
    "context_events": (
        "tenant_id", "event_id", "event_type", "object_type", "object_id", "object_version",
        "source_ref", "observed_at", "recorded_at", "recorded_by", "payload_json", "content_hash",
    ),
}
PRE_EXPERIENCE_TABLES = frozenset(TABLE_COLUMNS)
TABLE_COLUMNS.update({
    "agent_experiences": (
        "tenant_id", "experience_id", "execution_id", "evaluation_run_id", "agent_id",
        "agent_version", "agent_content_hash", "profile_id", "profile_version",
        "profile_content_hash", "workload_ref", "workload_hash", "execution_status",
        "evaluation_score", "evaluation_passed", "case_pass_rate", "avg_latency_ms",
        "total_cost_usd", "input_tokens", "output_tokens", "linkage_status",
        "evidence_status", "record_hash", "created_at", "created_by",
    ),
    "learning_lessons": (
        "tenant_id", "lesson_id", "experience_id", "lesson_type", "statement",
        "evidence_refs_json", "source_kind", "extractor_version", "confidence",
        "status", "statement_hash", "created_at", "created_by",
    ),
})
BASE_TABLES = frozenset({
    "agent_versions", "evaluation_profiles", "executions", "evaluation_runs", "tenant_audit",
})
TABLE_ORDER: dict[str, str] = {
    "agent_versions": "agent_id, version",
    "evaluation_profiles": "profile_id, version",
    "executions": "created_at, execution_id",
    "evaluation_runs": "created_at, run_id",
    "tenant_audit": "occurred_at, actor_id, action",
    "context_entities": "entity_id, version",
    "context_relationships": "relationship_id, version",
    "context_evidence": "entity_id, observed_at, evidence_id",
    "context_events": "recorded_at, event_id",
    "agent_experiences": "created_at, experience_id",
    "learning_lessons": "created_at, lesson_id",
}
TABLE_KEYS: dict[str, tuple[str, ...]] = {
    "agent_versions": ("tenant_id", "agent_id", "version"),
    "evaluation_profiles": ("tenant_id", "profile_id", "version"),
    "executions": ("execution_id",),
    "evaluation_runs": ("run_id",),
    "tenant_audit": (
        "tenant_id", "action", "previous_status", "new_status", "actor_id", "occurred_at",
    ),
    "context_entities": ("tenant_id", "entity_id", "version"),
    "context_relationships": ("tenant_id", "relationship_id", "version"),
    "context_evidence": ("tenant_id", "evidence_id"),
    "context_events": ("tenant_id", "event_id"),
    "agent_experiences": ("tenant_id", "experience_id"),
    "learning_lessons": ("tenant_id", "lesson_id"),
}


class BackupError(Exception):
    """Base failure for tenant backup or restore."""


class BackupKeyError(BackupError):
    """Backup key is missing, malformed, or unavailable."""


class BackupFormatError(BackupError):
    """Backup envelope or plaintext payload is invalid."""


class BackupConflictError(BackupError):
    """Restore would overwrite or mix existing tenant data."""


class BackupKeyProvider(Protocol):
    def active_key(self) -> tuple[str, bytes]:
        """Return the active key identifier and 256-bit key."""

    def key_for(self, key_id: str) -> bytes:
        """Resolve a historical key by identifier."""


class EnvironmentBackupKeyProvider:
    """Resolve externally managed keys from process environment only."""

    def __init__(self, environ: Mapping[str, str] | None = None):
        self.environ = os.environ if environ is None else environ

    @staticmethod
    def _key_name(key_id: str) -> str:
        if not isinstance(key_id, str) or not KEY_ID_PATTERN.fullmatch(key_id):
            raise BackupKeyError("Invalid AgentBox backup key identifier")
        return "AGENTBOX_BACKUP_KEY_" + key_id.upper()

    def key_for(self, key_id: str) -> bytes:
        encoded = self.environ.get(self._key_name(key_id))
        if not encoded:
            raise BackupKeyError(f"AgentBox backup key is unavailable: {key_id}")
        try:
            key = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as exc:
            raise BackupKeyError("AgentBox backup key must be standard base64") from exc
        if len(key) != 32:
            raise BackupKeyError("AgentBox backup key must decode to exactly 32 bytes")
        return key

    def active_key(self) -> tuple[str, bytes]:
        key_id = self.environ.get("AGENTBOX_BACKUP_KEY_ID", "")
        if not KEY_ID_PATTERN.fullmatch(key_id):
            raise BackupKeyError("AgentBox backup key ID is missing or invalid")
        return key_id, self.key_for(key_id)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def _aad(envelope: dict[str, object]) -> bytes:
    return _canonical({
        "format": envelope["format"],
        "version": envelope["version"],
        "algorithm": envelope["algorithm"],
        "key_id": envelope["key_id"],
        "tenant_id": envelope["tenant_id"],
    })


def _read_backup_envelope(path: Path) -> dict[str, object]:
    """Read a bounded regular backup file without following symlinks."""
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise BackupError("Backup source must be an accessible regular file") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise BackupError("Backup source must be an existing regular file")
        if metadata.st_size > 512 * 1024 * 1024:
            raise BackupFormatError("Tenant backup exceeds the 512 MiB safety limit")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, 512 * 1024 * 1024 + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > 512 * 1024 * 1024:
                raise BackupFormatError("Tenant backup exceeds the 512 MiB safety limit")
        return json.loads(b"".join(chunks).decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BackupFormatError("Invalid AgentBox tenant backup envelope") from exc
    finally:
        os.close(descriptor)


def _read_tenant_snapshot(database_path: str | Path, tenant_id: str) -> dict[str, object]:
    validate_tenant_id(tenant_id)
    connection = sqlite3.connect(str(database_path), timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    try:
        connection.execute("BEGIN")
        tenant = connection.execute(
            "SELECT tenant_id, display_name, status, created_at, updated_at, metadata_json "
            "FROM tenants WHERE tenant_id = ?", (tenant_id,)
        ).fetchone()
        if tenant is None:
            raise BackupError("Tenant does not exist in the AgentBox database")
        tables: dict[str, list[dict[str, object]]] = {}
        for table, columns in TABLE_COLUMNS.items():
            selected = ", ".join(f'"{column}"' for column in columns)
            rows = connection.execute(
                f'SELECT {selected} FROM "{table}" WHERE tenant_id = ? '
                f'ORDER BY {TABLE_ORDER[table]}',
                (tenant_id,),
            ).fetchall()
            tables[table] = [dict(row) for row in rows]
        payload: dict[str, object] = {
            "format": BACKUP_FORMAT,
            "version": BACKUP_VERSION,
            "tenant_id": tenant_id,
            "exported_at": datetime.now(UTC).isoformat(),
            "tenant": dict(tenant),
            "tables": tables,
        }
        payload["sha256"] = hashlib.sha256(_canonical(payload)).hexdigest()
        connection.rollback()
        return payload
    finally:
        connection.close()


def _atomic_private_write(path: str | Path, content: bytes) -> None:
    destination = Path(path)
    if destination.is_symlink():
        raise BackupError("Refusing to write a tenant backup through a symbolic link")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=destination.parent, prefix=".agentbox-backup-", delete=False
        ) as temporary:
            temporary_name = temporary.name
            os.chmod(temporary_name, 0o600)
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        try:
            os.link(temporary_name, destination)
        except FileExistsError as exc:
            raise BackupError("Backup destination already exists; refusing to overwrite") from exc
        os.chmod(destination, 0o600)
    finally:
        if temporary_name and os.path.exists(temporary_name):
            os.unlink(temporary_name)


class TenantBackupService:
    def __init__(self, database_path: str | Path, key_provider: BackupKeyProvider):
        self.database_path = str(database_path)
        self.key_provider = key_provider

    def export_encrypted(self, tenant_id: str, destination: str | Path) -> dict[str, object]:
        payload = _read_tenant_snapshot(self.database_path, tenant_id)
        key_id, key = self.key_provider.active_key()
        nonce = os.urandom(12)
        envelope: dict[str, object] = {
            "format": BACKUP_FORMAT,
            "version": BACKUP_VERSION,
            "algorithm": ALGORITHM,
            "key_id": key_id,
            "tenant_id": tenant_id,
            "nonce": base64.b64encode(nonce).decode("ascii"),
        }
        ciphertext = AESGCM(key).encrypt(nonce, _canonical(payload), _aad(envelope))
        envelope["ciphertext"] = base64.b64encode(ciphertext).decode("ascii")
        _atomic_private_write(destination, _canonical(envelope))
        return {
            "tenant_id": tenant_id,
            "key_id": key_id,
            "algorithm": ALGORITHM,
            "bytes_written": Path(destination).stat().st_size,
        }

    def restore_encrypted(self, tenant_id: str, source: str | Path) -> dict[str, int | str]:
        validate_tenant_id(tenant_id)
        path = Path(source)
        try:
            envelope = _read_backup_envelope(path)
            if not isinstance(envelope, dict):
                raise TypeError("envelope must be an object")
            required = {"format", "version", "algorithm", "key_id", "tenant_id", "nonce", "ciphertext"}
            if set(envelope) != required:
                raise ValueError("unexpected envelope fields")
            if (
                envelope["format"] != BACKUP_FORMAT
                or type(envelope["version"]) is not int
                or envelope["version"] != BACKUP_VERSION
            ):
                raise ValueError("unsupported backup format or version")
            if envelope["algorithm"] != ALGORITHM:
                raise ValueError("unsupported backup encryption algorithm")
            if (
                not isinstance(envelope["key_id"], str)
                or not KEY_ID_PATTERN.fullmatch(envelope["key_id"])
                or not isinstance(envelope["nonce"], str)
                or not isinstance(envelope["ciphertext"], str)
            ):
                raise ValueError("invalid backup key identifier or encoded fields")
            validate_tenant_id(envelope["tenant_id"])
            if envelope["tenant_id"] != tenant_id:
                raise ValueError("backup tenant does not match requested tenant")
            nonce = base64.b64decode(envelope["nonce"], validate=True)
            ciphertext = base64.b64decode(envelope["ciphertext"], validate=True)
            if len(nonce) != 12 or len(ciphertext) < 16:
                raise ValueError("invalid AES-GCM nonce or ciphertext length")
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
            raise BackupFormatError("Invalid AgentBox tenant backup envelope") from exc

        key = self.key_provider.key_for(envelope["key_id"])
        try:
            plaintext = AESGCM(key).decrypt(nonce, ciphertext, _aad(envelope))
            payload = json.loads(plaintext)
        except Exception as exc:
            raise BackupFormatError("Backup authentication failed or key is incorrect") from exc
        self._validate_payload(payload, tenant_id)
        return self._restore_payload(payload, tenant_id)

    @staticmethod
    def _validate_payload(payload: object, tenant_id: str) -> None:
        if not isinstance(payload, dict):
            raise BackupFormatError("Backup payload must be an object")
        expected_payload_fields = {
            "format", "version", "tenant_id", "exported_at", "tenant", "tables", "sha256"
        }
        if set(payload) != expected_payload_fields:
            raise BackupFormatError("Backup payload fields are invalid")
        if (
            payload.get("format") != BACKUP_FORMAT
            or type(payload.get("version")) is not int
            or payload.get("version") != BACKUP_VERSION
        ):
            raise BackupFormatError("Unsupported AgentBox backup payload")
        if not isinstance(payload.get("exported_at"), str):
            raise BackupFormatError("Backup export timestamp is invalid")
        if payload.get("tenant_id") != tenant_id:
            raise BackupFormatError("Backup payload tenant mismatch")
        digest = payload.get("sha256")
        unsigned = dict(payload)
        unsigned.pop("sha256", None)
        if not isinstance(digest, str) or not hashlib.sha256(_canonical(unsigned)).hexdigest() == digest:
            raise BackupFormatError("Backup payload integrity digest mismatch")
        tenant = payload.get("tenant")
        if not isinstance(tenant, dict) or tenant.get("tenant_id") != tenant_id:
            raise BackupFormatError("Backup tenant record is missing or mismatched")
        if tenant.get("status") not in {"pending", "active", "suspended", "decommissioned"}:
            raise BackupFormatError("Backup tenant lifecycle status is invalid")
        tables = payload.get("tables")
        if not isinstance(tables, dict) or frozenset(tables) not in {
            BASE_TABLES, PRE_EXPERIENCE_TABLES, frozenset(TABLE_COLUMNS)
        }:
            raise BackupFormatError("Backup table set is invalid")
        # v1 backups created before ECE existed contain only BASE_TABLES.
        for table in TABLE_COLUMNS:
            tables.setdefault(table, [])
        for table, rows in tables.items():
            if not isinstance(rows, list):
                raise BackupFormatError(f"Backup rows are invalid for {table}")
            for row in rows:
                if not isinstance(row, dict) or set(row) != set(TABLE_COLUMNS[table]):
                    raise BackupFormatError(f"Backup row shape is invalid for {table}")
                if row.get("tenant_id") != tenant_id:
                    raise BackupFormatError(f"Cross-tenant row detected in {table}")
                for value in row.values():
                    if (
                        value is not None
                        and not isinstance(value, (str, int, float, bool))
                    ):
                        raise BackupFormatError(f"Backup row value is invalid for {table}")
                    if (
                        isinstance(value, int)
                        and not isinstance(value, bool)
                        and not -(2**63) <= value < 2**63
                    ):
                        raise BackupFormatError(f"Backup integer is out of range for {table}")
                    if isinstance(value, float) and not math.isfinite(value):
                        raise BackupFormatError(f"Backup number is not finite for {table}")
        if not isinstance(tenant.get("metadata_json"), str):
            raise BackupFormatError("Backup tenant metadata is invalid")
        try:
            metadata = json.loads(tenant["metadata_json"])
            if not isinstance(metadata, dict):
                raise TypeError("metadata is not an object")
        except (ValueError, TypeError) as exc:
            raise BackupFormatError("Backup tenant metadata is invalid") from exc

    def _restore_payload(self, payload: dict[str, object], tenant_id: str) -> dict[str, int | str]:
        counts = {table: 0 for table in TABLE_COLUMNS}
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing_tenant = connection.execute(
                "SELECT tenant_id FROM tenants WHERE tenant_id = ?", (tenant_id,)
            ).fetchone()
            if existing_tenant is None:
                raise BackupError("Restore target tenant must be provisioned before restore")
            for table in (
                "agent_versions", "evaluation_profiles", "executions", "evaluation_runs",
                "tenant_audit", "context_entities", "context_relationships",
                "context_evidence", "context_events", "agent_experiences", "learning_lessons",
            ):
                columns = TABLE_COLUMNS[table]
                keys = TABLE_KEYS[table]
                quoted_columns = ", ".join(f'"{column}"' for column in columns)
                where = " AND ".join(f'"{column}" IS ?' for column in keys)
                for row in payload["tables"][table]:
                    key_values = tuple(row[column] for column in keys)
                    current = connection.execute(
                        f'SELECT {quoted_columns} FROM "{table}" WHERE {where}', key_values
                    ).fetchone()
                    values = tuple(row[column] for column in columns)
                    if current is not None:
                        if tuple(current[column] for column in columns) != values:
                            raise BackupConflictError(
                                f"Restore conflict in {table}; existing row differs from backup"
                            )
                        continue
                    insert_columns = columns
                    insert_values = values
                    if table == "tenant_audit":
                        insert_columns = tuple(column for column in columns if column != "audit_id")
                        insert_values = tuple(row[column] for column in insert_columns)
                    insert_names = ", ".join(f'"{column}"' for column in insert_columns)
                    insert_placeholders = ", ".join("?" for _ in insert_columns)
                    connection.execute(
                        f'INSERT INTO "{table}" ({insert_names}) VALUES ({insert_placeholders})',
                        insert_values,
                    )
                    counts[table] += 1
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return {"tenant_id": tenant_id, **counts}

from __future__ import annotations

import sqlite3

import pytest

from app.capability_registry import (
    CapabilityConflictError,
    CapabilityNotFoundError,
    CapabilityRegistry,
    CapabilityValidationError,
)


def contract(version="1.0.0"):
    return {
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


def registry(tmp_path):
    store = CapabilityRegistry(tmp_path / "agentbox.sqlite3")
    with store._connection() as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS tenants (tenant_id TEXT PRIMARY KEY)")
        connection.execute("INSERT OR IGNORE INTO tenants VALUES ('tenant-a')")
        connection.execute("INSERT OR IGNORE INTO tenants VALUES ('tenant-b')")
    return store


def test_register_is_idempotent_and_content_addressed(tmp_path):
    store = registry(tmp_path)
    first, created = store.register(contract(), "tenant-a")
    replay, replay_created = store.register(contract(), "tenant-a")
    assert created is True and replay_created is False
    assert first.content_hash == replay.content_hash
    assert store.get("crm.contact.read", "1.0.0", "tenant-a") == first
    store.close()


def test_semantic_version_prerelease_and_build_metadata(tmp_path):
    store = registry(tmp_path)
    record, created = store.register(contract("1.2.3-rc.1+build.5"), "tenant-a")
    assert created is True
    assert record.version == "1.2.3-rc.1+build.5"
    store.close()


def test_version_is_immutable(tmp_path):
    store = registry(tmp_path)
    store.register(contract(), "tenant-a")
    changed = contract()
    changed["description"] = "Changed meaning."
    with pytest.raises(CapabilityConflictError):
        store.register(changed, "tenant-a")
    store.close()


def test_tenant_isolation(tmp_path):
    store = registry(tmp_path)
    store.register(contract(), "tenant-a")
    with pytest.raises(CapabilityNotFoundError):
        store.get("crm.contact.read", "1.0.0", "tenant-b")
    store.close()


def test_invalid_contract_rejected(tmp_path):
    store = registry(tmp_path)
    invalid = contract()
    invalid["timeout_seconds"] = 0
    with pytest.raises(CapabilityValidationError):
        store.register(invalid, "tenant-a")
    store.close()


def test_sqlite_guards_prevent_update_and_delete(tmp_path):
    store = registry(tmp_path)
    store.register(contract(), "tenant-a")
    with store._connection() as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute("UPDATE capability_versions SET version='2.0.0'")
    with store._connection() as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute("DELETE FROM capability_versions")
    store.close()


def test_rejects_missing_tenant(tmp_path):
    store = registry(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        store.register(contract(), "not-provisioned")
    store.close()



def test_list_detects_tampered_persisted_contract(tmp_path):
    from app.capability_registry import CapabilityRegistryError

    store = registry(tmp_path)
    store.register(contract(), "tenant-a")
    with store._connection() as connection:
        connection.execute("DROP TRIGGER capability_versions_no_update")
        connection.execute(
            "UPDATE capability_versions SET contract_json=? WHERE tenant_id=?",
            ('{"capability_id":"tampered"}', "tenant-a"),
        )
    with pytest.raises(CapabilityRegistryError, match="content hash mismatch"):
        store.list("tenant-a")
    store.close()

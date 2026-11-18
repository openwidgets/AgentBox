import sqlite3

import pytest

from app.cognitive_runtime import (
    CognitiveRunConflictError,
    CognitiveRunError,
    CognitiveRunNotFoundError,
    CognitiveRunService,
    CognitiveRunValidationError,
)
from app.tenant_registry import TenantRegistry

HASH = "a" * 64


def make_service(tmp_path):
    path = tmp_path / "cognitive.sqlite3"
    tenants = TenantRegistry(path)
    tenants.create("tenant-alpha", "Alpha", "test")
    tenants.transition("tenant-alpha", "active", "test")
    tenants.create("tenant-beta", "Beta", "test")
    tenants.transition("tenant-beta", "active", "test")
    return CognitiveRunService(path), tenants


def create_run(service, key="create-1", tenant="tenant-alpha"):
    return service.create_run(
        "goal:lead-triage@1.0.0", "runtime.test@1.0.0", "runtime.test",
        "1.0.0", HASH, tenant, "user-1", key,
    )


def test_create_run_writes_first_intent_event_and_is_idempotent(tmp_path):
    service, tenants = make_service(tmp_path)
    run, event, created = create_run(service)
    assert created is True
    assert run["current_stage"] == "intent"
    assert run["next_sequence"] == 2
    assert event["sequence"] == 1
    assert event["event_type"] == "intent.accepted"
    replay, replay_event, created_again = create_run(service)
    assert created_again is False
    assert replay["run_id"] == run["run_id"]
    assert replay_event["event_id"] == event["event_id"]
    tenants.close()
    service.close()


def test_create_idempotency_key_rejects_different_request(tmp_path):
    service, tenants = make_service(tmp_path)
    create_run(service)
    with pytest.raises(CognitiveRunConflictError, match="reused"):
        service.create_run(
            "goal:different", "runtime.test@1.0.0", "runtime.test",
            "1.0.0", HASH, "tenant-alpha", "user-1", "create-1",
        )
    tenants.close()
    service.close()


def test_events_are_sequenced_and_event_replay_is_idempotent(tmp_path):
    service, tenants = make_service(tmp_path)
    run, _, _ = create_run(service)
    payload = {
        "stage": "context",
        "event_type": "context.assembled",
        "evidence_status": "unverified",
        "payload": {"context_ref": "context:snapshot-1"},
    }
    first, created = service.append_event(run["run_id"], payload, "tenant-alpha", "user-1", "event-1")
    replay, replay_created = service.append_event(
        run["run_id"], payload, "tenant-alpha", "user-1", "event-1"
    )
    assert created is True
    assert replay_created is False
    assert first["event_id"] == replay["event_id"]
    assert first["sequence"] == 2
    assert service.get_run(run["run_id"], "tenant-alpha")["current_stage"] == "context"
    assert [event["sequence"] for event in service.list_events(run["run_id"], "tenant-alpha")] == [1, 2]
    tenants.close()
    service.close()


def test_event_idempotency_conflict_and_transition_skip_are_rejected(tmp_path):
    service, tenants = make_service(tmp_path)
    run, _, _ = create_run(service)
    service.append_event(
        run["run_id"], {"stage": "context", "event_type": "context.assembled"},
        "tenant-alpha", "user-1", "event-1",
    )
    with pytest.raises(CognitiveRunConflictError, match="reused"):
        service.append_event(
            run["run_id"], {"stage": "context", "event_type": "context.insufficient"},
            "tenant-alpha", "user-1", "event-1",
        )
    with pytest.raises(CognitiveRunValidationError, match="skip"):
        service.append_event(
            run["run_id"], {"stage": "capability", "event_type": "capability.rejected"},
            "tenant-alpha", "user-1", "event-2",
        )
    tenants.close()
    service.close()


def test_tenant_scope_hides_other_tenant_runs(tmp_path):
    service, tenants = make_service(tmp_path)
    run, _, _ = create_run(service)
    with pytest.raises(CognitiveRunNotFoundError):
        service.get_run(run["run_id"], "tenant-beta")
    with pytest.raises(CognitiveRunNotFoundError):
        service.list_events(run["run_id"], "tenant-beta")
    tenants.close()
    service.close()


def test_verified_memory_event_completes_run_and_blocks_more_events(tmp_path):
    service, tenants = make_service(tmp_path)
    run, _, _ = create_run(service)
    transitions = [
        ("context", "context.assembled"),
        ("cognition", "cognition.completed"),
        ("capability", "capability.selected"),
        ("responsibility", "responsibility.authorized"),
        ("execution", "execution.completed"),
        ("observation", "observation.recorded"),
        ("verification", "verification.passed"),
    ]
    for index, (stage, event_type) in enumerate(transitions, start=1):
        payload = {}
        if event_type == "capability.selected":
            payload["capability_ref"] = "crm.contact.read@1.0.0"
        if event_type == "responsibility.authorized":
            payload["authorization_decision_ref"] = "audit:decision-1"
        event = {
            "stage": stage,
            "event_type": event_type,
            "evidence_status": "verified" if stage == "verification" else "unverified",
            "evidence_refs": ["evidence:verified-1"] if stage == "verification" else [],
            "payload": payload,
        }
        service.append_event(run["run_id"], event, "tenant-alpha", "user-1", f"event-{index}")
    memory, _ = service.append_event(
        run["run_id"],
        {
            "stage": "memory",
            "event_type": "memory.accepted",
            "evidence_status": "verified",
            "evidence_refs": ["evidence:lesson-1"],
        },
        "tenant-alpha", "user-1", "event-memory",
    )
    assert memory["sequence"] == 9
    assert service.get_run(run["run_id"], "tenant-alpha")["status"] == "completed"
    with pytest.raises(CognitiveRunConflictError, match="completed"):
        service.append_event(
            run["run_id"], {"stage": "memory", "event_type": "memory.rejected"},
            "tenant-alpha", "user-1", "after-complete",
        )
    tenants.close()
    service.close()


def test_cognitive_event_journal_is_append_only(tmp_path):
    service, tenants = make_service(tmp_path)
    run, _, _ = create_run(service)
    connection = service._connect()
    try:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"), connection:
            connection.execute(
                "UPDATE cognitive_events SET event_type='intent.rejected' WHERE run_id=?",
                (run["run_id"],),
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"), connection:
            connection.execute(
                "DELETE FROM cognitive_events WHERE run_id=?", (run["run_id"],)
            )
    finally:
        connection.close()
    tenants.close()
    service.close()


def test_cognitive_store_closes_idempotently(tmp_path):
    service, tenants = make_service(tmp_path)
    service.close()
    service.close()
    with pytest.raises(RuntimeError, match="closed"):
        service._connect()
    tenants.close()


def test_event_payload_rejects_nested_authority_and_executable_fields(tmp_path):
    service, tenants = make_service(tmp_path)
    run, _, _ = create_run(service)
    with pytest.raises(CognitiveRunValidationError, match="forbidden"):
        service.append_event(
            run["run_id"],
            {
                "stage": "context",
                "event_type": "context.assembled",
                "payload": {"metadata": {"execute": "untrusted"}},
            },
            "tenant-alpha", "user-1", "unsafe-event",
        )
    assert len(service.list_events(run["run_id"], "tenant-alpha")) == 1
    tenants.close()
    service.close()


def test_idempotency_keys_are_stored_only_as_hashes(tmp_path):
    service, tenants = make_service(tmp_path)
    run, _, _ = create_run(service, key="sensitive-client-key")
    service.append_event(
        run["run_id"],
        {"stage": "context", "event_type": "context.assembled"},
        "tenant-alpha", "user-1", "sensitive-event-key",
    )
    connection = service._connect()
    try:
        create_hash = connection.execute(
            "SELECT create_idempotency_key_hash FROM cognitive_runs WHERE run_id=?",
            (run["run_id"],),
        ).fetchone()[0]
        event_hash = connection.execute(
            "SELECT idempotency_key_hash FROM cognitive_events WHERE run_id=? AND sequence=2",
            (run["run_id"],),
        ).fetchone()[0]
    finally:
        connection.close()
    assert create_hash != "sensitive-client-key"
    assert event_hash != "sensitive-event-key"
    assert len(create_hash) == len(event_hash) == 64
    tenants.close()
    service.close()


def test_event_envelope_digest_is_verified_on_read(tmp_path):
    service, tenants = make_service(tmp_path)
    run, _, _ = create_run(service)
    connection = service._connect()
    try:
        with connection:
            connection.execute("DROP TRIGGER cognitive_events_no_update")
            connection.execute(
                "UPDATE cognitive_events SET event_json='{}' WHERE run_id=?",
                (run["run_id"],),
            )
    finally:
        connection.close()
    with pytest.raises(CognitiveRunError, match="integrity"):
        service.list_events(run["run_id"], "tenant-alpha")
    tenants.close()
    service.close()

import sqlite3

import pytest

from app.experiment_comparison import (
    ExperimentComparisonConflictError,
    ExperimentComparisonNotFoundError,
    ExperimentComparisonService,
    ExperimentComparisonValidationError,
)


@pytest.fixture
def comparison_db(tmp_path):
    path = tmp_path / "comparison.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE tenants (tenant_id TEXT PRIMARY KEY);
        INSERT INTO tenants VALUES ('tenant-a');
        INSERT INTO agent_versions (
            tenant_id, agent_id, version, content_hash, manifest_json,
            parent_artifact_id, created_at
        ) VALUES
            ('tenant-a', 'agent-a', '1.0.0', 'a-parent', '{}', NULL, '2026-01-01T00:00:00Z'),
            ('tenant-a', 'agent-a', '1.1.0', 'b-candidate', '{}', 'agent-a@1.0.0', '2026-01-02T00:00:00Z');
        CREATE TABLE evaluation_profiles (
            tenant_id TEXT, profile_id TEXT, version TEXT, content_hash TEXT
        );
        INSERT INTO evaluation_profiles VALUES ('tenant-a', 'quality', '1.0.0', 'profile-hash');
        CREATE TABLE evaluation_runs (
            tenant_id TEXT, run_id TEXT, agent_id TEXT, agent_version TEXT,
            profile_id TEXT, profile_version TEXT, status TEXT, result_json TEXT
        );
        INSERT INTO evaluation_runs VALUES
            ('tenant-a', 'parent-run', 'agent-a', '1.0.0', 'quality', '1.0.0', 'completed',
             '{"run_id":"parent-run","profile_id":"quality","profile_version":"1.0.0","score":0.7,"passed":false,"avg_latency_ms":1000,"total_cost_usd":0.02}'),
            ('tenant-a', 'candidate-run', 'agent-a', '1.1.0', 'quality', '1.0.0', 'completed',
             '{"run_id":"candidate-run","profile_id":"quality","profile_version":"1.0.0","score":0.9,"passed":true,"avg_latency_ms":800,"total_cost_usd":0.015}');
    """)
    connection.commit()
    connection.close()
    return path


@pytest.fixture
def service(comparison_db):
    instance = ExperimentComparisonService(comparison_db)
    yield instance
    instance.close()


def request(**overrides):
    values = {
        "agent_id": "agent-a",
        "parent_version": "1.0.0",
        "candidate_version": "1.1.0",
        "parent_evaluation_run_id": "parent-run",
        "candidate_evaluation_run_id": "candidate-run",
        "workload_ref": "benchmark://quality/v1",
        "workload_hash": "a" * 64,
        "tenant_id": "tenant-a",
        "actor_id": "reviewer",
    }
    values.update(overrides)
    return values


def test_comparison_pins_parent_candidate_profile_and_workload(service):
    record, created = service.compare(**request())

    assert created is True
    assert record["evidence_status"] == "unverified"
    assert record["promotion_eligible"] is False
    assert record["decision_authority"] == "advisory_only"
    assert record["comparison"]["asserted_same_workload"] is True
    assert record["comparison"]["workload_provenance"] == "caller_asserted"
    assert record["comparison"]["score"] == {
        "parent": 0.7, "candidate": 0.9, "delta": 0.2,
    }
    assert record["comparison"]["outcome"] == "candidate_score_higher"
    assert record["comparison"]["avg_latency_ms"]["delta"] == -200
    assert record["comparison"]["total_cost_usd"]["delta"] == -0.005


def test_comparison_is_idempotent_for_same_immutable_pair(service):
    first, created = service.compare(**request())
    replay, replay_created = service.compare(**request())

    assert created is True
    assert replay_created is False
    assert replay["comparison_id"] == first["comparison_id"]


def test_comparison_rejects_same_evaluation_run(service):
    with pytest.raises(ExperimentComparisonValidationError):
        service.compare(**request(candidate_evaluation_run_id="parent-run"))


def test_comparison_rejects_candidate_without_exact_parent_lineage(comparison_db):
    connection = sqlite3.connect(comparison_db)
    connection.execute(
        "UPDATE agent_versions SET parent_artifact_id = 'agent-a@0.9.0' WHERE version = '1.1.0'"
    )
    connection.commit()
    connection.close()
    service = ExperimentComparisonService(comparison_db)
    try:
        with pytest.raises(ExperimentComparisonValidationError, match="lineage"):
            service.compare(**request())
    finally:
        service.close()


def test_comparison_rejects_mismatched_profile_version(comparison_db):
    connection = sqlite3.connect(comparison_db)
    connection.execute(
        "UPDATE evaluation_runs SET profile_version = '2.0.0' WHERE run_id = 'candidate-run'"
    )
    connection.commit()
    connection.close()
    service = ExperimentComparisonService(comparison_db)
    try:
        with pytest.raises(ExperimentComparisonValidationError, match="exact same profile"):
            service.compare(**request())
    finally:
        service.close()


def test_comparison_rejects_evaluation_run_for_wrong_agent_version(comparison_db):
    connection = sqlite3.connect(comparison_db)
    connection.execute(
        "UPDATE evaluation_runs SET agent_version = '1.0.0' WHERE run_id = 'candidate-run'"
    )
    connection.commit()
    connection.close()
    service = ExperimentComparisonService(comparison_db)
    try:
        with pytest.raises(ExperimentComparisonValidationError, match="requested agent version"):
            service.compare(**request())
    finally:
        service.close()


def test_comparison_is_tenant_scoped(service):
    with pytest.raises(ExperimentComparisonNotFoundError):
        service.compare(**request(tenant_id="tenant-b"))
    record, _ = service.compare(**request())
    with pytest.raises(ExperimentComparisonNotFoundError):
        service.get(record["comparison_id"], "tenant-b")


def test_comparison_rejects_invalid_workload_hash(service):
    with pytest.raises(ExperimentComparisonValidationError, match="SHA-256"):
        service.compare(**request(workload_hash="not-a-hash"))


def test_comparison_rejects_conflicting_replay(comparison_db):
    service = ExperimentComparisonService(comparison_db)
    try:
        service.compare(**request())
        with pytest.raises(ExperimentComparisonConflictError):
            service.compare(**request(workload_ref="benchmark://different/v1"))
    finally:
        service.close()

def test_comparison_records_are_append_only(comparison_db):
    service = ExperimentComparisonService(comparison_db)
    try:
        record, _ = service.compare(**request())
    finally:
        service.close()

    connection = sqlite3.connect(comparison_db)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        connection.execute(
            "UPDATE agent_experiment_comparisons SET workload_ref = ? WHERE comparison_id = ?",
            ("benchmark://mutated", record["comparison_id"]),
        )
    connection.rollback()
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        connection.execute(
            "DELETE FROM agent_experiment_comparisons WHERE comparison_id = ?",
            (record["comparison_id"],),
        )
    connection.close()


def test_comparison_rejects_same_parent_and_candidate_version(service):
    with pytest.raises(ExperimentComparisonValidationError, match="versions must differ"):
        service.compare(**request(candidate_version="1.0.0"))

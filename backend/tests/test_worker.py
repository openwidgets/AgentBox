from __future__ import annotations

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest
from auth_helpers import configure_test_access
from fastapi.testclient import TestClient

from app.execution import DeterministicFakeProvider
from app.main import create_app
from app.worker import CapabilityInvocationContext, ExecutionJobWorker


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "AGENTBOX_JOB_ALLOWED_HANDLERS",
        json.dumps([{
            "workload_kind": "provider_invocation",
            "handler_id": "provider.fake",
            "handler_version": "1.0.0",
        }]),
    )
    app = configure_test_access(create_app(str(tmp_path / "worker.sqlite3")))
    app.state.executions.providers["provider.fake"] = DeterministicFakeProvider()
    return TestClient(app)


def create_agent(client):
    root = Path(__file__).resolve().parents[2]
    manifest = json.loads((root / "examples" / "agent-manifest-v1.json").read_text())
    manifest["agent_id"] = "runtime.test"
    manifest["model_binding"]["provider_id"] = "provider.fake"
    manifest["tools"] = []
    response = client.post("/v1/agents", json={"manifest": manifest})
    assert response.status_code == 201


def submit(client, key="worker-test-job"):
    return client.post(
        "/v1/agents/runtime.test/versions/1.0.0/jobs",
        headers={"Idempotency-Key": key},
        json={"input": {"prompt": "hello"}, "max_output_tokens": 64},
    )


def make_worker(client, worker_id="test-worker", capability_handlers=None, secret_broker=None):
    state = client.app.state
    return ExecutionJobWorker(
        state.execution_jobs, state.registry, state.executions,
        capabilities=state.capabilities, capability_handlers=capability_handlers,
        secret_broker=secret_broker,
        worker_id=worker_id, lease_seconds=6, poll_interval_seconds=0.05,
    )


def test_worker_dispatches_registered_provider_job(client):
    create_agent(client)
    submitted = submit(client)
    assert submitted.status_code == 202

    worker = make_worker(client)
    assert worker.run_once() is True
    job = client.get(f"/v1/execution-jobs/{submitted.json()['job_id']}").json()
    assert job["state"] == "succeeded"
    assert job["result"]["execution"]["provider_id"] == "provider.fake"
    assert job["result"]["execution"]["output"]["input"] == {"prompt": "hello"}
    assert worker.run_once() is False


def test_worker_rejects_sandbox_workload(client):
    worker = make_worker(client)
    sandbox_job = {
        "workload_kind": "sandbox_workload",
        "handler_id": "sandbox.gvisor",
        "handler_version": "1.0.0",
    }
    with pytest.raises(ValueError, match="does not support"):
        worker._dispatch(sandbox_job)


def test_worker_fails_closed_when_workload_hash_changes(client):
    create_agent(client)
    submitted = submit(client, "worker-hash-test")
    job_id = submitted.json()["job_id"]
    with client.app.state.execution_jobs._connect() as connection:
        connection.execute(
            "UPDATE execution_jobs SET workload_hash=? WHERE job_id=?",
            (hashlib.sha256(b"tampered").hexdigest(), job_id),
        )
    assert make_worker(client).run_once() is True
    job = client.get(f"/v1/execution-jobs/{job_id}").json()
    assert job["state"] == "failed"
    assert job["error_code"] == "provider_execution_failed"


def test_worker_executes_openai_provider_job_through_mocked_http(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "AGENTBOX_JOB_ALLOWED_HANDLERS",
        json.dumps([{
            "workload_kind": "provider_invocation",
            "handler_id": "provider.openai",
            "handler_version": "1.0.0",
        }]),
    )
    monkeypatch.setenv("AGENTBOX_OPENAI_API_KEY", "test-only-secret")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AGENTBOX_OPENAI_INPUT_USD_PER_MILLION", raising=False)
    monkeypatch.delenv("AGENTBOX_OPENAI_OUTPUT_USD_PER_MILLION", raising=False)

    class MockHTTPResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _size=-1):
            return json.dumps({
                "choices": [{"message": {"content": "worker provider result"}}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 4},
            }).encode()

    monkeypatch.setattr(
        "app.openai_provider.urlopen",
        lambda *_args, **_kwargs: MockHTTPResponse(),
    )
    app = configure_test_access(create_app(str(tmp_path / "openai-worker.sqlite3")))
    client = TestClient(app)

    root = Path(__file__).resolve().parents[2]
    manifest = json.loads((root / "examples" / "agent-manifest-v1.json").read_text())
    manifest["agent_id"] = "runtime.test"
    manifest["model_binding"]["provider_id"] = "provider.openai"
    manifest["tools"] = []
    created = client.post("/v1/agents", json={"manifest": manifest})
    assert created.status_code == 201

    submitted = client.post(
        "/v1/agents/runtime.test/versions/1.0.0/jobs",
        headers={"Idempotency-Key": "openai-worker-integration"},
        json={"input": {"prompt": "hello from durable worker"}, "max_output_tokens": 48},
    )
    assert submitted.status_code == 202

    state = client.app.state
    worker = ExecutionJobWorker(
        state.execution_jobs, state.registry, state.executions,
        worker_id="openai-test-worker", lease_seconds=6, poll_interval_seconds=0.05,
    )
    assert worker.run_once() is True

    job = client.get(f"/v1/execution-jobs/{submitted.json()['job_id']}").json()
    assert job["state"] == "succeeded"
    execution = job["result"]["execution"]
    assert execution["provider_id"] == "provider.openai"
    assert execution["output"] == "worker provider result"
    assert execution["usage"]["input_tokens"] == 11
    assert execution["usage"]["output_tokens"] == 4
    assert execution["usage"]["cost_usd"] is None

def test_worker_waits_for_heartbeat_thread_before_returning(client):
    create_agent(client)
    submitted = submit(client, "worker-heartbeat-shutdown")
    worker = make_worker(client)
    heartbeat_started = Event()
    release_heartbeat = Event()

    def blocked_heartbeat(_lease, _stop, _cancel_requested, _lease_lost):
        heartbeat_started.set()
        release_heartbeat.wait(timeout=5)

    worker._maintain_lease = blocked_heartbeat
    with ThreadPoolExecutor(max_workers=1) as executor:
        result = executor.submit(worker.run_once)
        assert heartbeat_started.wait(timeout=2)
        assert result.done() is False
        release_heartbeat.set()
        assert result.result(timeout=3) is True

    job = client.get(f"/v1/execution-jobs/{submitted.json()['job_id']}").json()
    assert job["state"] == "succeeded"




def capability_contract(timeout_seconds=8):
    return {
        "schema_version": "1.0.0",
        "capability_id": "crm.contact.read",
        "version": "1.0.0",
        "name": "Read contact",
        "description": "Read one tenant-owned contact.",
        "input_schema": {
            "type": "object",
            "properties": {"contact_id": {"type": "string", "minLength": 1}},
            "required": ["contact_id"],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        },
        "auth_ref": "policy:crm.contact.read",
        "permission_profile": {"principal_scope": "user", "actions": ["crm.contact.read"]},
        "timeout_seconds": timeout_seconds,
        "audit": {
            "required": True,
            "events": ["requested", "authorized", "completed", "failed"],
            "record_input": False,
            "record_output": False,
        },
    }


def enqueue_capability(client, key="capability-worker-job", timeout_seconds=8):
    state = client.app.state
    artifact, _ = state.capabilities.register(capability_contract(timeout_seconds), "test-tenant")
    job, _ = state.execution_jobs.create(
        tenant_id="test-tenant",
        subject_id="test-user",
        principal_type="user",
        workload_kind="provider_invocation",
        handler_id="provider.fake",
        handler_version="1.0.0",
        workload_ref="capability:crm.contact.read@1.0.0",
        workload_hash=artifact.content_hash,
        payload={
            "capability_id": "crm.contact.read",
            "version": "1.0.0",
            "input": {"contact_id": "contact-123"},
        },
        idempotency_key=key,
    )
    return job


def capability_test_read_contact(context, value):
    context.raise_if_expired()
    if context.tenant_id != "test-tenant" or context.subject_id != "test-user":
        raise ValueError("unexpected invocation identity")
    if context.timeout_seconds != 8 or value != {"contact_id": "contact-123"}:
        raise ValueError("unexpected invocation contract")
    return {"name": "Ada"}


def capability_test_invalid_output(_context, _value):
    return {"name": 42}


def capability_test_slow_handler(context, _value):
    time.sleep(context.timeout_seconds + 2)
    return {"name": "Too late"}


def capability_test_non_json_output(_context, _value):
    return object()


class CapabilityTestSecretBroker:
    def resolve(self, request):
        if (
            request.tenant_id != "test-tenant"
            or request.subject_id != "test-user"
            or request.capability_id != "crm.contact.read"
            or request.capability_version != "1.0.0"
            or request.secret_ref != "secret://crm/contact-reader"
            or request.purpose != "crm.api.read"
            or len(request.contract_hash) != 64
        ):
            raise PermissionError("secret scope mismatch")
        return "short-lived-test-secret"


def capability_test_secret_handler(context, _value):
    secret = context.resolve_secret("secret://crm/contact-reader", "crm.api.read")
    return {"name": "authorized" if secret == "short-lived-test-secret" else "denied"}


def test_worker_executes_capability_through_static_handler_and_validates_output(client):
    job = enqueue_capability(client)
    worker = make_worker(
        client,
        capability_handlers={("provider.fake", "1.0.0"): capability_test_read_contact},
    )
    assert worker.run_once() is True
    completed = client.app.state.execution_jobs.get(job["job_id"], "test-tenant")
    assert completed["state"] == "succeeded"
    assert completed["result"] == {
        "capability_id": "crm.contact.read",
        "version": "1.0.0",
        "output": {"name": "Ada"},
    }


def test_capability_secret_broker_receives_exact_invocation_scope(client):
    job = enqueue_capability(client, "capability-secret-scope")
    worker = make_worker(
        client,
        capability_handlers={( "provider.fake", "1.0.0"): capability_test_secret_handler},
        secret_broker=CapabilityTestSecretBroker(),
    )
    assert worker.run_once() is True
    completed = client.app.state.execution_jobs.get(job["job_id"], "test-tenant")
    assert completed["state"] == "succeeded"
    assert completed["result"]["output"] == {"name": "authorized"}


def test_capability_secret_resolution_fails_closed_without_broker():
    context = CapabilityInvocationContext(
        tenant_id="tenant-a",
        subject_id="user-a",
        capability_id="crm.contact.read",
        version="1.0.0",
        content_hash="a" * 64,
        timeout_seconds=5,
        deadline_monotonic=time.monotonic() + 5,
    )
    with pytest.raises(PermissionError, match="not configured"):
        context.resolve_secret("secret://crm/contact-reader", "crm.api.read")
    with pytest.raises(ValueError, match="opaque"):
        context.resolve_secret("not-a-secret-ref", "crm.api.read")


def test_worker_fails_capability_job_when_handler_result_is_not_json(client):
    job = enqueue_capability(client, "capability-non-json-output")
    worker = make_worker(
        client,
        capability_handlers={( "provider.fake", "1.0.0"): capability_test_non_json_output},
    )
    assert worker.run_once() is True
    failed = client.app.state.execution_jobs.get(job["job_id"], "test-tenant")
    assert failed["state"] == "failed"
    assert failed["error_code"] == "provider_execution_failed"


def test_worker_fails_capability_job_without_static_handler(client):
    job = enqueue_capability(client, "capability-no-handler")
    assert make_worker(client).run_once() is True
    failed = client.app.state.execution_jobs.get(job["job_id"], "test-tenant")
    assert failed["state"] == "failed"
    assert failed["error_code"] == "provider_execution_failed"


def test_worker_fails_capability_job_when_handler_output_violates_contract(client):
    job = enqueue_capability(client, "capability-invalid-output")
    worker = make_worker(
        client,
        capability_handlers={("provider.fake", "1.0.0"): capability_test_invalid_output},
    )
    assert worker.run_once() is True
    failed = client.app.state.execution_jobs.get(job["job_id"], "test-tenant")
    assert failed["state"] == "failed"
    assert failed["error_code"] == "provider_execution_failed"



def test_capability_invocation_context_exposes_cooperative_deadline(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("app.worker.time.monotonic", lambda: now[0])
    context = CapabilityInvocationContext(
        tenant_id="tenant-a",
        subject_id="user-a",
        capability_id="crm.contact.read",
        version="1.0.0",
        content_hash="a" * 64,
        timeout_seconds=5,
        deadline_monotonic=105.0,
    )
    assert context.remaining_seconds() == 5.0
    context.raise_if_expired()
    now[0] = 104.5
    assert context.remaining_seconds() == 0.5
    context.raise_if_expired()
    now[0] = 105.0
    assert context.remaining_seconds() == 0.0
    with pytest.raises(TimeoutError, match="deadline exceeded"):
        context.raise_if_expired()


def test_worker_terminates_capability_handler_at_hard_deadline(client):
    job = enqueue_capability(client, "capability-hard-timeout", timeout_seconds=3)
    worker = make_worker(
        client,
        capability_handlers={("provider.fake", "1.0.0"): capability_test_slow_handler},
    )
    started = time.monotonic()
    assert worker.run_once() is True
    elapsed = time.monotonic() - started
    failed = client.app.state.execution_jobs.get(job["job_id"], "test-tenant")
    assert failed["state"] == "failed"
    assert failed["error_code"] == "provider_execution_failed"
    assert elapsed < 5

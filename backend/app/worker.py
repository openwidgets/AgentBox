"""Dedicated worker for trusted AgentBox provider-invocation jobs.

This process dispatches only explicitly registered provider handlers. It does not
interpret agent manifests as code and intentionally rejects sandbox workloads.
"""
from __future__ import annotations

import json
import logging
import multiprocessing
import os
import signal
import threading
import time
import uuid
from dataclasses import dataclass, field
from collections.abc import Callable, Mapping
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema import ValidationError as JSONSchemaValidationError

from .capability_process_limits import apply_capability_process_limits
from .capability_registry import CapabilityRegistry
from .capability_secrets import (
    CapabilitySecretBroker,
    CapabilitySecretRequest,
    DenyAllCapabilitySecretBroker,
    validate_secret_request,
)
from .execution import ExecutionService
from .execution_jobs import DurableExecutionJobService, JobLeaseError
from .registry import AgentRegistry

LOGGER = logging.getLogger("agentbox.worker")


@dataclass(frozen=True)
class CapabilityInvocationContext:
    """Trusted, immutable metadata supplied to a statically registered handler."""

    tenant_id: str
    subject_id: str
    capability_id: str
    version: str
    content_hash: str
    timeout_seconds: int
    deadline_monotonic: float
    secret_broker: CapabilitySecretBroker = field(default_factory=DenyAllCapabilitySecretBroker, repr=False, compare=False)

    def resolve_secret(self, secret_ref: str, purpose: str) -> str:
        """Resolve one opaque reference through the scoped broker; never use ambient env secrets."""
        validate_secret_request(secret_ref, purpose)
        request = CapabilitySecretRequest(
            tenant_id=self.tenant_id,
            subject_id=self.subject_id,
            capability_id=self.capability_id,
            capability_version=self.version,
            contract_hash=self.content_hash,
            secret_ref=secret_ref,
            purpose=purpose,
        )
        value = self.secret_broker.resolve(request)
        if not isinstance(value, str) or not value or len(value) > 65536:
            raise PermissionError("capability secret broker returned an invalid grant")
        return value

    def remaining_seconds(self) -> float:
        """Return the remaining cooperative execution budget in seconds."""
        return max(0.0, self.deadline_monotonic - time.monotonic())

    def raise_if_expired(self) -> None:
        """Allow handlers to enforce the deadline at safe cancellation checkpoints."""
        if self.remaining_seconds() <= 0:
            raise TimeoutError("capability execution deadline exceeded")


CapabilityHandler = Callable[[CapabilityInvocationContext, Any], Any]


def _capability_handler_child(connection: Any, handler: CapabilityHandler,
                              context: CapabilityInvocationContext, value: Any) -> None:
    """Run one importable trusted handler in a disposable spawned process."""
    try:
        apply_capability_process_limits(context.timeout_seconds)
        context.raise_if_expired()
        result = handler(context, value)
        context.raise_if_expired()
        envelope = json.dumps({"ok": True, "result": result}, ensure_ascii=False,
                              allow_nan=False, separators=(",", ":")).encode("utf-8")
        if len(envelope) > 1_048_576:
            raise ValueError("capability handler result exceeds 1 MiB")
        connection.send_bytes(envelope)
    except Exception:
        try:
            connection.send_bytes(b'{"ok":false}')
        except (BrokenPipeError, OSError):
            pass
    finally:
        connection.close()


def _stop_capability_process(process: multiprocessing.Process) -> None:
    """Reap a handler process; escalate to kill if graceful termination stalls."""
    if process.pid is None:
        return
    if process.is_alive():
        process.terminate()
        process.join(timeout=1)
    if process.is_alive():
        process.kill()
        process.join(timeout=5)
    if process.is_alive():
        raise RuntimeError("capability handler process could not be reaped")


def _run_capability_handler_isolated(
    handler: CapabilityHandler,
    context: CapabilityInvocationContext,
    value: Any,
) -> Any:
    """Execute a trusted importable handler with a parent-enforced hard deadline."""
    process_context = multiprocessing.get_context("spawn")
    receive, send = process_context.Pipe(duplex=False)
    process = process_context.Process(
        target=_capability_handler_child,
        args=(send, handler, context, value),
        daemon=True,
    )
    try:
        process.start()
        send.close()
        if not receive.poll(context.remaining_seconds()):
            _stop_capability_process(process)
            raise TimeoutError("capability handler exceeded its execution deadline")
        try:
            payload = receive.recv_bytes(1_048_576)
        except EOFError as exc:
            raise RuntimeError("capability handler process exited without a result") from exc
        process.join(timeout=1)
        if process.is_alive():
            _stop_capability_process(process)
            raise RuntimeError("capability handler did not exit after returning a result")
        try:
            result = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("capability handler returned an invalid result envelope") from exc
        if not isinstance(result, dict) or result.get("ok") is not True or "result" not in result:
            raise RuntimeError("capability handler failed")
        return result["result"]
    finally:
        send.close()
        receive.close()
        if process.pid is not None:
            if process.is_alive():
                _stop_capability_process(process)
            if not process.is_alive():
                process.close()


# Deployments add reviewed Python callables here. No contract implementation_ref
# or environment-provided import path is ever loaded.
CAPABILITY_HANDLERS: dict[tuple[str, str], CapabilityHandler] = {}
# Deployments may replace this with a reviewed broker client. The default denies all.
CAPABILITY_SECRET_BROKER: CapabilitySecretBroker = DenyAllCapabilitySecretBroker()


class ExecutionJobWorker:
    """Single-job-at-a-time worker using the durable job service as its authority."""

    def __init__(
        self,
        jobs: DurableExecutionJobService,
        registry: AgentRegistry,
        executions: ExecutionService,
        *,
        capabilities: CapabilityRegistry | None = None,
        capability_handlers: Mapping[tuple[str, str], CapabilityHandler] | None = None,
        secret_broker: CapabilitySecretBroker | None = None,
        worker_id: str | None = None,
        lease_seconds: int = 30,
        poll_interval_seconds: float = 1.0,
    ) -> None:
        if not 3 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be between 3 and 3600")
        if not 0.05 <= poll_interval_seconds <= 60:
            raise ValueError("poll_interval_seconds must be between 0.05 and 60")
        self.jobs = jobs
        self.registry = registry
        self.executions = executions
        self.capabilities = capabilities
        self.capability_handlers = dict(capability_handlers or {})
        self.secret_broker = secret_broker if secret_broker is not None else DenyAllCapabilitySecretBroker()
        allowed_capability_handlers = {
            (handler_id, handler_version)
            for kind, handler_id, handler_version in self.jobs.allowed_handlers
            if kind == "provider_invocation"
        }
        if set(self.capability_handlers) - allowed_capability_handlers:
            raise ValueError("capability handler registry contains a handler outside the trusted allowlist")
        if not all(callable(handler) for handler in self.capability_handlers.values()):
            raise TypeError("capability handlers must be callable")
        self.worker_id = worker_id or f"worker-{uuid.uuid4()}"
        if not self.worker_id or len(self.worker_id) > 128:
            raise ValueError("worker_id must be between 1 and 128 characters")
        self.lease_seconds = lease_seconds
        self.poll_interval_seconds = poll_interval_seconds

    def run_once(self) -> bool:
        """Claim and process at most one job; return False when the queue is empty."""
        claim = self.jobs.claim(self.worker_id, self.lease_seconds)
        if claim is None:
            return False

        job_id = claim["job_id"]
        tenant_id = claim["tenant_id"]
        lease_token = claim["lease_token"]
        lease = {
            "job_id": job_id,
            "tenant_id": tenant_id,
            "worker_id": self.worker_id,
            "lease_token": lease_token,
        }
        try:
            started = self.jobs.start(job_id, tenant_id, self.worker_id, lease_token)
            claim["attempt"] = started["attempt"]
        except JobLeaseError:
            LOGGER.warning("Worker lost lease before start for job %s", job_id)
            return True

        stop_heartbeat = threading.Event()
        cancel_requested = threading.Event()
        lease_lost = threading.Event()
        heartbeat_thread = threading.Thread(
            target=self._maintain_lease,
            args=(lease, stop_heartbeat, cancel_requested, lease_lost),
            name=f"agentbox-heartbeat-{job_id}",
            daemon=True,
        )
        heartbeat_thread.start()
        try:
            result = self._dispatch(claim)
            if lease_lost.is_set():
                LOGGER.warning(
                    "Lease lost during execution for job %s; leaving outcome for recovery",
                    job_id,
                )
                return True
            current = self.jobs.get(job_id, tenant_id)
            if cancel_requested.is_set() or current["state"] == "cancel_requested":
                self.jobs.confirm_cancel(
                    job_id, tenant_id, self.worker_id, self.worker_id, lease_token
                )
                return True
            self.jobs.complete(job_id, tenant_id, self.worker_id, lease_token, result)
        except JobLeaseError:
            LOGGER.warning("Lease lost while finalizing job %s; leaving outcome for recovery", job_id)
        except Exception:
            LOGGER.exception("Trusted handler failed for job %s", job_id)
            if not lease_lost.is_set():
                if cancel_requested.is_set():
                    try:
                        self.jobs.confirm_cancel(
                            job_id, tenant_id, self.worker_id, self.worker_id, lease_token
                        )
                    except JobLeaseError:
                        LOGGER.warning("Could not confirm cancellation for job %s", job_id)
                else:
                    try:
                        self.jobs.fail(
                            job_id, tenant_id, self.worker_id, lease_token,
                            "provider_execution_failed",
                        )
                    except JobLeaseError:
                        LOGGER.warning("Could not record failure after lease loss for job %s", job_id)
        finally:
            stop_heartbeat.set()
            # Do not let shutdown close the job store while heartbeat I/O is
            # still in flight; SQLite may legitimately wait up to busy_timeout.
            heartbeat_thread.join()
        return True

    def _maintain_lease(
        self,
        lease: dict[str, str],
        stop: threading.Event,
        cancel_requested: threading.Event,
        lease_lost: threading.Event,
    ) -> None:
        interval = max(0.5, self.lease_seconds / 3)
        while not stop.wait(interval):
            try:
                self.jobs.heartbeat(
                    lease["job_id"], lease["tenant_id"], self.worker_id,
                    lease["lease_token"], self.lease_seconds,
                )
                current = self.jobs.get(lease["job_id"], lease["tenant_id"])
                if current["state"] == "cancel_requested":
                    # Provider adapters are synchronous; do not claim termination until
                    # the invocation returns and this worker can safely confirm it.
                    cancel_requested.set()
            except Exception:
                lease_lost.set()
                LOGGER.exception("Lease heartbeat failed for job %s", lease["job_id"])
                return

    def _dispatch(self, job: dict[str, Any]) -> dict[str, Any]:
        if job["workload_kind"] != "provider_invocation":
            raise ValueError("worker does not support this workload kind")
        if isinstance(job.get("workload_ref"), str) and job["workload_ref"].startswith("capability:"):
            return self._dispatch_capability(job)
        allowed = {
            (provider_id, version)
            for kind, provider_id, version in self.jobs.allowed_handlers
            if kind == "provider_invocation"
        }
        if (job["handler_id"], job["handler_version"]) not in allowed:
            raise ValueError("provider handler is not in the trusted allowlist")

        agent_id, separator, version = job["workload_ref"].rpartition("@")
        if not separator or not agent_id or not version:
            raise ValueError("workload reference is invalid")
        artifact = self.registry.get(agent_id, version, job["tenant_id"])
        if artifact.content_hash != job["workload_hash"]:
            raise ValueError("registered workload hash does not match admitted job")
        binding = artifact.manifest.get("model_binding", {})
        if binding.get("provider_id") != job["handler_id"]:
            raise ValueError("manifest provider does not match the admitted handler")
        if artifact.manifest.get("tools"):
            raise ValueError("tool-enabled manifests are not supported by this worker")
        payload = job.get("payload")
        if not isinstance(payload, dict) or set(payload) != {"input", "max_output_tokens"}:
            raise ValueError("provider job payload is invalid")

        execution = self.executions.execute(
            agent_id,
            version,
            payload["input"],
            idempotency_key=f"job:{job['job_id']}:attempt:{job['attempt']}",
            max_output_tokens=payload["max_output_tokens"],
            tenant_id=job["tenant_id"],
        )
        return {
            "execution_id": execution["execution_id"],
            "execution": execution["result"],
        }


    def _dispatch_capability(self, job: dict[str, Any]) -> dict[str, Any]:
        """Execute through a reviewed, statically registered capability callable."""
        if self.capabilities is None:
            raise ValueError("capability registry is not configured in this worker")
        capability_ref = job["workload_ref"][len("capability:"):]
        capability_id, separator, version = capability_ref.rpartition("@")
        if not separator or not capability_id or not version:
            raise ValueError("capability workload reference is invalid")
        payload = job.get("payload")
        if not isinstance(payload, dict) or set(payload) != {"capability_id", "version", "input"}:
            raise ValueError("capability job payload is invalid")
        if payload["capability_id"] != capability_id or payload["version"] != version:
            raise ValueError("capability payload does not match its workload reference")

        artifact = self.capabilities.get(capability_id, version, job["tenant_id"])
        if artifact.content_hash != job["workload_hash"]:
            raise ValueError("capability contract hash does not match admitted job")
        contract = artifact.contract
        try:
            Draft202012Validator(contract["input_schema"]).validate(payload["input"])
        except JSONSchemaValidationError as exc:
            raise ValueError("capability input no longer matches its immutable contract") from exc

        handler_key = (job["handler_id"], job["handler_version"])
        allowed = {
            (handler_id, handler_version)
            for kind, handler_id, handler_version in self.jobs.allowed_handlers
            if kind == "provider_invocation"
        }
        if handler_key not in allowed:
            raise ValueError("capability handler is not in the trusted allowlist")
        handler = self.capability_handlers.get(handler_key)
        if handler is None:
            raise ValueError("no reviewed capability handler is registered for this handler version")

        context = CapabilityInvocationContext(
            tenant_id=job["tenant_id"],
            subject_id=job["subject_id"],
            capability_id=capability_id,
            version=version,
            content_hash=artifact.content_hash,
            timeout_seconds=contract["timeout_seconds"],
            deadline_monotonic=time.monotonic() + contract["timeout_seconds"],
            secret_broker=self.secret_broker,
        )
        output = _run_capability_handler_isolated(handler, context, payload["input"])
        try:
            Draft202012Validator(contract["output_schema"]).validate(output)
        except JSONSchemaValidationError as exc:
            raise ValueError("capability handler output does not match its immutable contract") from exc
        return {
            "capability_id": capability_id,
            "version": version,
            "output": output,
        }

    def run_forever(self, stop_event: threading.Event | None = None) -> None:
        """Poll until stopped; in-flight work is allowed to finish."""
        stop = stop_event or threading.Event()
        while not stop.is_set():
            if not self.run_once():
                stop.wait(self.poll_interval_seconds)


def main() -> None:
    """Start the standalone worker using the same configured SQLite database as the API."""
    import logging

    logging.basicConfig(
        level=os.getenv("AGENTBOX_WORKER_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    from .main import create_app

    app = create_app()
    worker = ExecutionJobWorker(
        app.state.execution_jobs,
        app.state.registry,
        app.state.executions,
        capabilities=app.state.capabilities,
        capability_handlers=CAPABILITY_HANDLERS,
        secret_broker=CAPABILITY_SECRET_BROKER,
        worker_id=os.getenv("AGENTBOX_WORKER_ID") or None,
        lease_seconds=int(os.getenv("AGENTBOX_WORKER_LEASE_SECONDS", "30")),
        poll_interval_seconds=float(os.getenv("AGENTBOX_WORKER_POLL_INTERVAL_SECONDS", "1")),
    )
    stop = threading.Event()

    def request_shutdown(signum: int, frame: Any) -> None:
        del signum, frame
        LOGGER.info("Shutdown requested; waiting for current job to finish")
        stop.set()

    signal.signal(signal.SIGINT, request_shutdown)
    signal.signal(signal.SIGTERM, request_shutdown)
    LOGGER.info("Starting worker %s", worker.worker_id)
    try:
        worker.run_forever(stop)
    finally:
        app.state.close_stores()
        LOGGER.info("Worker %s stopped", worker.worker_id)


if __name__ == "__main__":
    main()

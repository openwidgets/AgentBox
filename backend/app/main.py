"""AgentBox API: manifest registry and versioned evaluation service."""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from jsonschema import Draft202012Validator
from jsonschema import ValidationError as JSONSchemaValidationError
from pydantic import BaseModel, ConfigDict, Field

from .authentication import require_authenticated_principal
from .authorization import authorize_capability
from .authorization_audit import AuthorizationAuditStore
from .authorization_policy import validate_authorization_policy
from .candidate_generation import (
    CandidateConflictError,
    CandidateGenerationService,
    CandidateValidationError,
)
from .capability_registry import (
    CapabilityConflictError,
    CapabilityNotFoundError,
    CapabilityRegistry,
    CapabilityValidationError,
)
from .cognitive_runtime import (
    CognitiveRunConflictError,
    CognitiveRunNotFoundError,
    CognitiveRunService,
    CognitiveRunValidationError,
)
from .context_assembly import ContextAssemblyService, ContextAssemblyValidationError
from .enterprise_context import (
    ContextConflictError,
    ContextNotFoundError,
    ContextValidationError,
    EnterpriseContextService,
)
from .evaluation import (
    EvaluationProfileConflictError,
    EvaluationProfileNotFoundError,
    EvaluationRunNotFoundError,
    EvaluationService,
)
from .execution import (
    DeterministicFakeProvider,
    ExecutionConflictError,
    ExecutionNotFoundError,
    ExecutionPolicyError,
    ExecutionService,
    ProviderError,
)
from .execution_jobs import (
    DurableExecutionJobService,
    JobConflictError,
    JobLeaseError,
    JobNotFoundError,
)
from .experience import (
    ExecutionExperienceService,
    ExperienceConflictError,
    ExperienceNotFoundError,
    ExperienceValidationError,
)
from .experiment_comparison import (
    ExperimentComparisonConflictError,
    ExperimentComparisonNotFoundError,
    ExperimentComparisonService,
    ExperimentComparisonValidationError,
)
from .openai_provider import OpenAICompatibleProvider
from .postgresql_connectors import PostgreSQLConnectorRegistry
from .postgresql_ingestion import PostgreSQLSnapshotIngestion
from .postgresql_provenance import PostgreSQLProvenanceVerifier
from .principal import Principal
from .registry import AgentNotFoundError, AgentRegistry, VersionConflictError
from .tenant_registry import TenantNotFoundError, TenantRegistry
from .validation import validate_evaluation_profile, validate_manifest


def require_active_tenant_principal(
    request: Request,
    principal: Principal = Depends(require_authenticated_principal),  # noqa: B008
) -> Principal:
    """Require an OIDC principal whose server-owned tenant is currently active."""
    if principal.tenant_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"message": "Tenant context is required", "reason": "missing_tenant"},
        )
    try:
        tenant = request.app.state.tenants.get(principal.tenant_id)
    except (TenantNotFoundError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"message": "Tenant is not provisioned", "reason": "tenant_not_provisioned"},
        ) from exc
    if not tenant.is_accessible:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"message": "Tenant is not active", "reason": "tenant_inactive"},
        )
    return principal


AUTHENTICATED_PRINCIPAL = Depends(require_active_tenant_principal)


class CapabilityContractRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contract: dict[str, Any]


class CapabilityInvocationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input: Any


class ManifestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    manifest: dict[str, Any]


class EvaluationProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile: dict[str, Any]


class CloneRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    new_version: str = Field(
        pattern=r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
    )
    overrides: dict[str, Any] = Field(default_factory=dict)


class CandidateGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_version: str = Field(
        pattern=r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
    )
    operator_ids: list[str] = Field(min_length=1, max_length=5)
    seed: int = Field(ge=0, le=4294967295)


class ExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input: Any
    max_output_tokens: int = Field(default=1024, ge=1, le=8192)


class ExecutionJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input: Any
    max_output_tokens: int = Field(default=1024, ge=1, le=8192)


class WorkerClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    worker_id: str = Field(min_length=1, max_length=128)
    lease_seconds: int = Field(default=30, ge=1, le=3600)


class WorkerRecoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    supervisor_id: str = Field(default="recovery", min_length=1, max_length=128)


class WorkerLeaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: str = Field(min_length=2, max_length=128)
    worker_id: str = Field(min_length=1, max_length=128)
    lease_token: str = Field(min_length=1, max_length=128)


class WorkerCompleteRequest(WorkerLeaseRequest):
    result: dict[str, Any]


class WorkerCancelConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: str = Field(min_length=2, max_length=128)
    actor_id: str = Field(min_length=1, max_length=128)
    worker_id: str | None = Field(default=None, min_length=1, max_length=128)
    lease_token: str | None = Field(default=None, min_length=1, max_length=128)


class WorkerFailRequest(WorkerLeaseRequest):
    error_code: str = Field(min_length=1, max_length=128)


class EvaluationCaseSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str = Field(min_length=1, max_length=128)
    output: Any
    latency_ms: float = Field(ge=0)
    cost_usd: float = Field(ge=0)


class EvaluationRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_id: str = Field(min_length=3, max_length=128)
    profile_version: str = Field(min_length=1, max_length=128)
    results: list[EvaluationCaseSubmission] = Field(max_length=500)


class EvaluationExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_id: str = Field(min_length=3, max_length=128)
    profile_version: str = Field(min_length=1, max_length=128)
    max_output_tokens: int = Field(default=1024, ge=1, le=8192)


class ExperienceRecordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    execution_id: str = Field(min_length=1, max_length=128)
    evaluation_run_id: str = Field(min_length=1, max_length=128)
    workload_ref: str = Field(min_length=1, max_length=512)
    workload_hash: str = Field(pattern=r"^[a-fA-F0-9]{64}$")


class LearningLessonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lesson_type: str = Field(min_length=2, max_length=64)
    statement: str = Field(min_length=1, max_length=2000)
    evidence_refs: list[str] = Field(min_length=1, max_length=20)
    source_kind: Literal["deterministic_rule", "human_review", "model_proposal"]
    extractor_version: str = Field(min_length=1, max_length=128)
    confidence: float = Field(ge=0, le=1)


class ExperimentComparisonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: str = Field(min_length=1, max_length=128)
    parent_version: str = Field(min_length=1, max_length=128)
    candidate_version: str = Field(min_length=1, max_length=128)
    parent_evaluation_run_id: str = Field(min_length=1, max_length=128)
    candidate_evaluation_run_id: str = Field(min_length=1, max_length=128)
    workload_ref: str = Field(min_length=1, max_length=512)
    workload_hash: str = Field(pattern=r"^[a-fA-F0-9]{64}$")


class CognitiveRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    goal_ref: str = Field(min_length=1, max_length=1024)
    agent_ref: str = Field(min_length=3, max_length=512)


class CognitiveEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stage: str = Field(min_length=1, max_length=32)
    event_type: str = Field(min_length=3, max_length=64)
    evidence_status: Literal["unverified", "verified", "not_applicable"] = "unverified"
    input_refs: list[str] = Field(default_factory=list, max_length=100)
    output_refs: list[str] = Field(default_factory=list, max_length=100)
    evidence_refs: list[str] = Field(default_factory=list, max_length=100)
    payload: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = Field(default=None, min_length=2, max_length=128)
    causation_event_id: str | None = None
    correlation_id: str | None = Field(default=None, min_length=1, max_length=128)


class CognitiveContextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity_ids: list[str] | None = Field(default=None, min_length=1, max_length=12)
    search: str | None = Field(default=None, min_length=1, max_length=128)
    include_inferences: bool = False
    limit: int = Field(default=8, ge=1, le=12)


class PostgreSQLConnectorRegistrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connector_id: str = Field(min_length=2, max_length=128)
    source_ref: str = Field(min_length=1, max_length=1024)
    key_id: str = Field(min_length=2, max_length=128)
    public_key: str = Field(min_length=40, max_length=64)


class PostgreSQLConnectorKeyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key_id: str = Field(min_length=2, max_length=128)
    public_key: str = Field(min_length=40, max_length=64)


class PostgreSQLConnectorAttestationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    algorithm: Literal["Ed25519"]
    key_id: str = Field(min_length=1, max_length=128)
    signed_at: str = Field(min_length=20, max_length=40)
    signature: str = Field(min_length=80, max_length=128)


class PostgreSQLSnapshotIngestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_ref: str = Field(min_length=1, max_length=1024)
    snapshot: dict[str, Any]
    connector_id: str | None = Field(default=None, min_length=2, max_length=128)
    attestation: PostgreSQLConnectorAttestationRequest | None = None


class ContextEntityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity_id: str = Field(min_length=2, max_length=128)
    entity_type: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=1, max_length=256)
    description: str = Field(default="", max_length=2000)
    attributes: dict[str, Any]
    assertion_class: Literal["observed_fact", "inferred", "ai_hypothesis", "human_validated"]
    confidence: float = Field(ge=0, le=1)
    valid_from: str | None = None
    valid_to: str | None = None


class ContextEntityVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    entity: ContextEntityRequest


class ContextRelationshipRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    relationship_id: str = Field(min_length=2, max_length=128)
    relationship_type: str = Field(min_length=2, max_length=64)
    source_entity_id: str = Field(min_length=2, max_length=128)
    source_version: int = Field(ge=1)
    target_entity_id: str = Field(min_length=2, max_length=128)
    target_version: int = Field(ge=1)
    attributes: dict[str, Any] = Field(default_factory=dict)
    assertion_class: Literal["observed_fact", "inferred", "ai_hypothesis", "human_validated"]
    confidence: float = Field(ge=0, le=1)
    valid_from: str | None = None
    valid_to: str | None = None


class ContextEvidenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_id: str = Field(min_length=2, max_length=128)
    entity_id: str = Field(min_length=2, max_length=128)
    entity_version: int = Field(ge=1)
    relationship_id: str | None = Field(default=None, min_length=2, max_length=128)
    relationship_version: int | None = Field(default=None, ge=1)
    source_kind: Literal[
        "database_metadata", "api_metadata", "repository_metadata",
        "configuration_metadata", "event_log", "human_attestation",
        "agent_analysis", "other"
    ]
    source_ref: str = Field(min_length=1, max_length=1024)
    source_hash: str | None = Field(default=None, pattern=r"^[a-fA-F0-9]{64}$")
    observed_at: str
    confidence: float = Field(ge=0, le=1)
    validation_status: Literal["unverified"] = "unverified"
    metadata: dict[str, Any] = Field(default_factory=dict)



class ContextEvidenceReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["validated", "rejected"]
    reason: str = Field(min_length=10, max_length=2000)
    confirmed_source_hash: str | None = Field(default=None, pattern=r"^[a-fA-F0-9]{64}$")


MUTABLE_MANIFEST_FIELDS = {
    "name", "description", "purpose", "behavior", "model_binding",
    "workflow_ref", "memory", "knowledge_sources", "policy_refs",
    "dependencies", "runtime_targets", "evaluation_profile_refs", "tags",
}


def merge_overrides(target: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    for key, value in overrides.items():
        if key in target and isinstance(target[key], dict) and isinstance(value, dict):
            target[key] = merge_overrides(copy.deepcopy(target[key]), value)
        else:
            target[key] = copy.deepcopy(value)
    return target


def record_response(record: Any) -> dict[str, Any]:
    return {
        "agent_id": record.agent_id,
        "version": record.version,
        "content_hash": record.content_hash,
        "manifest": record.manifest,
        "parent_artifact_id": record.parent_artifact_id,
        "created_at": record.created_at,
        "artifact_id": f"{record.agent_id}@{record.version}",
    }



def load_authorization_policies() -> list[dict[str, Any]]:
    """Load validated explicit policies; an absent file means deny all."""
    configured = os.getenv("AGENTBOX_AUTHORIZATION_POLICIES_PATH")
    if not configured:
        return []
    try:
        import json

        policies = json.loads(Path(configured).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("Unable to load AgentBox authorization policies") from exc
    if not isinstance(policies, list):
        raise TypeError("AgentBox authorization policy document must be a JSON array")
    for index, policy in enumerate(policies):
        if not isinstance(policy, dict):
            raise TypeError(f"AgentBox authorization policy at index {index} is not an object")
        errors = validate_authorization_policy(policy)
        if errors:
            raise RuntimeError(
                f"Invalid AgentBox authorization policy at index {index}: {errors}"
            )
    return policies


def enforce_authorization(
    app: FastAPI,
    principal: Principal,
    capability_id: str,
    action: str,
    resource_id: str,
) -> None:
    decision = authorize_capability(
        principal,
        capability_id,
        action,
        resource_id,
        app.state.authorization_policies,
    )
    if hasattr(app.state, "authorization_audit"):
        # Audit both grants and denials; failure to persist the decision fails closed.
        app.state.authorization_audit.append(
            principal, capability_id, action, resource_id, decision
        )
    if not decision["allowed"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"message": "Access denied", "reason": decision["reason"]},
        )


def load_job_handler_allowlist() -> set[tuple[str, str, str]]:
    """Load exact workload handlers from trusted server configuration."""
    raw = os.getenv("AGENTBOX_JOB_ALLOWED_HANDLERS", "[]")
    try:
        entries = json.loads(raw)
    except ValueError as exc:
        raise RuntimeError("AGENTBOX_JOB_ALLOWED_HANDLERS must be valid JSON") from exc
    if not isinstance(entries, list):
        raise TypeError("AGENTBOX_JOB_ALLOWED_HANDLERS must be a JSON array")
    allowed: set[tuple[str, str, str]] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {
            "workload_kind", "handler_id", "handler_version"
        }:
            raise ValueError("Each job handler entry must contain exactly workload_kind, handler_id and handler_version")
        values = (entry["workload_kind"], entry["handler_id"], entry["handler_version"])
        if not all(isinstance(value, str) and value.strip() for value in values):
            raise ValueError("Job handler fields must be non-empty strings")
        if values[0] not in {"provider_invocation", "sandbox_workload"}:
            raise ValueError("Unsupported job workload kind")
        allowed.add(values)
    return allowed



def load_capability_handler_bindings(
    allowed_handlers: set[tuple[str, str, str]],
) -> dict[tuple[str, str], tuple[str, str, str]]:
    """Bind immutable capability versions to exact server-allowlisted handlers."""
    raw = os.getenv("AGENTBOX_CAPABILITY_HANDLERS", "[]")
    try:
        entries = json.loads(raw)
    except ValueError as exc:
        raise RuntimeError("AGENTBOX_CAPABILITY_HANDLERS must be valid JSON") from exc
    if not isinstance(entries, list):
        raise TypeError("AGENTBOX_CAPABILITY_HANDLERS must be a JSON array")
    bindings: dict[tuple[str, str], tuple[str, str, str]] = {}
    required = {
        "capability_id", "version", "workload_kind", "handler_id", "handler_version"
    }
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != required:
            raise ValueError("Each capability handler entry must contain exactly capability_id, version, workload_kind, handler_id and handler_version")
        capability_id, version = entry["capability_id"], entry["version"]
        handler = (entry["workload_kind"], entry["handler_id"], entry["handler_version"])
        if not all(isinstance(value, str) and value.strip() for value in (capability_id, version, *handler)):
            raise ValueError("Capability handler binding fields must be non-empty strings")
        key = (capability_id, version)
        if key in bindings:
            raise ValueError(f"Duplicate capability handler binding: {capability_id}@{version}")
        if handler not in allowed_handlers:
            raise ValueError(f"Capability handler is not present in AGENTBOX_JOB_ALLOWED_HANDLERS: {handler[1]}@{handler[2]}")
        bindings[key] = handler
    return bindings


def require_job_worker(authorization: str | None) -> None:
    """Authenticate internal worker endpoints with a dedicated server secret."""
    expected = os.getenv("AGENTBOX_JOB_WORKER_TOKEN")
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Execution worker authentication is not configured",
        )
    parts = authorization.split() if authorization else []
    if (
        len(parts) != 2
        or parts[0].lower() != "bearer"
        or not hmac.compare_digest(parts[1], expected)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Execution worker authentication failed",
            headers={"WWW-Authenticate": "Bearer"},
        )


def create_app(database_path: str | None = None) -> FastAPI:
    app = FastAPI(
        title="AgentBox API",
        version="0.2.0",
        description=(
            "Frontend-independent API for versioned agent manifests and "
            "deterministic evaluation of submitted agent outputs."
        ),
    )
    db_path = database_path or os.getenv(
        "AGENTBOX_DATABASE_PATH",
        str(Path(__file__).resolve().parents[1] / "data" / "agentbox.sqlite3"),
    )
    app.state.authorization_policies = load_authorization_policies()
    app.state.authorization_audit = AuthorizationAuditStore(db_path)
    app.state.capabilities = CapabilityRegistry(db_path)
    app.state.job_handler_allowlist = load_job_handler_allowlist()
    app.state.capability_handler_bindings = load_capability_handler_bindings(
        app.state.job_handler_allowlist
    )
    app.state.execution_jobs = DurableExecutionJobService(
        db_path, allowed_handlers=app.state.job_handler_allowlist
    )
    app.state.tenants = TenantRegistry(db_path)
    app.state.registry = AgentRegistry(db_path)
    app.state.evaluations = EvaluationService(db_path)
    configured_providers = [DeterministicFakeProvider()]
    openai_provider = OpenAICompatibleProvider.from_environment()
    if openai_provider is not None:
        configured_providers.append(openai_provider)
    app.state.executions = ExecutionService(
        db_path, app.state.registry, providers=configured_providers
    )
    app.state.experiences = ExecutionExperienceService(db_path)
    app.state.cognitive_runs = CognitiveRunService(db_path)
    app.state.candidate_generation = CandidateGenerationService(app.state.registry)
    app.state.experiment_comparisons = ExperimentComparisonService(db_path)
    app.state.enterprise_context = EnterpriseContextService(db_path)
    app.state.context_assembly = ContextAssemblyService(app.state.enterprise_context)
    app.state.postgresql_ingestion = PostgreSQLSnapshotIngestion(app.state.enterprise_context)
    app.state.postgresql_connectors = PostgreSQLConnectorRegistry(db_path)

    def close_stores() -> None:
        """Close every API-owned SQLite store during process shutdown."""
        for store in (
            app.state.execution_jobs,
            app.state.executions,
            app.state.experiences,
            app.state.experiment_comparisons,
            app.state.cognitive_runs,
            app.state.enterprise_context,
            app.state.evaluations,
            app.state.registry,
            app.state.tenants,
            app.state.authorization_audit,
            app.state.capabilities,
            app.state.postgresql_connectors,
        ):
            store.close()

    app.state.close_stores = close_stores
    app.add_event_handler("shutdown", close_stores)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "agentbox-api"}

    @app.post("/v1/capabilities", status_code=status.HTTP_201_CREATED)
    def register_capability(
        request: CapabilityContractRequest,
        principal: Principal = AUTHENTICATED_PRINCIPAL,
    ) -> dict[str, Any]:
        capability_id = request.contract.get("capability_id", "")
        enforce_authorization(app, principal, "capability.registry", "capability.register", capability_id)
        try:
            record, created = app.state.capabilities.register(request.contract, principal.tenant_id)
        except CapabilityValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except CapabilityConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {
            "capability_id": record.capability_id, "version": record.version,
            "content_hash": record.content_hash, "contract": record.contract,
            "created_at": record.created_at, "created": created,
        }

    @app.get("/v1/capabilities")
    def list_capabilities(
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        principal: Principal = AUTHENTICATED_PRINCIPAL,
    ) -> dict[str, Any]:
        enforce_authorization(app, principal, "capability.registry", "capability.read", "*")
        records = app.state.capabilities.list(principal.tenant_id, limit, offset)
        return {
            "items": [{
                "capability_id": item.capability_id, "version": item.version,
                "content_hash": item.content_hash, "contract": item.contract,
                "created_at": item.created_at,
            } for item in records],
            "limit": limit, "offset": offset,
        }

    @app.get("/v1/capabilities/{capability_id}/versions/{version}")
    def get_capability(
        capability_id: str, version: str,
        principal: Principal = AUTHENTICATED_PRINCIPAL,
    ) -> dict[str, Any]:
        enforce_authorization(app, principal, "capability.registry", "capability.read", capability_id)
        try:
            record = app.state.capabilities.get(capability_id, version, principal.tenant_id)
        except CapabilityNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {
            "capability_id": record.capability_id, "version": record.version,
            "content_hash": record.content_hash, "contract": record.contract,
            "created_at": record.created_at,
        }



    @app.post(
        "/v1/capabilities/{capability_id}/versions/{version}/invocations",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def submit_capability_invocation(
        capability_id: str,
        version: str,
        request: CapabilityInvocationRequest,
        idempotency_key: str = Header(alias="Idempotency-Key"),
        principal: Principal = AUTHENTICATED_PRINCIPAL,
    ) -> dict[str, Any]:
        try:
            artifact = app.state.capabilities.get(
                capability_id, version, principal.tenant_id
            )
        except CapabilityNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Capability version not found") from exc

        contract = artifact.contract
        permission = contract.get("permission_profile", {})
        actions = permission.get("actions", [])
        if (
            permission.get("principal_scope") != principal.principal_type
            or len(actions) != 1
            or not isinstance(actions[0], str)
        ):
            raise HTTPException(
                status_code=422,
                detail="Capability invocation requires a matching principal scope and exactly one declared permission action",
            )

        binding = app.state.capability_handler_bindings.get((capability_id, version))
        if binding is None:
            raise HTTPException(
                status_code=503,
                detail="No trusted server handler is configured for this capability version",
            )

        try:
            Draft202012Validator(contract["input_schema"]).validate(request.input)
        except JSONSchemaValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail={"message": "Capability input does not match its contract", "path": list(exc.absolute_path)},
            ) from exc

        enforce_authorization(
            app, principal, capability_id, actions[0], capability_id
        )
        workload_kind, handler_id, handler_version = binding
        try:
            record, created = app.state.execution_jobs.create(
                tenant_id=principal.tenant_id,
                subject_id=principal.subject_id,
                principal_type=principal.principal_type,
                workload_kind=workload_kind,
                handler_id=handler_id,
                handler_version=handler_version,
                workload_ref=f"capability:{capability_id}@{version}",
                workload_hash=artifact.content_hash,
                payload={
                    "capability_id": capability_id,
                    "version": version,
                    "input": request.input,
                },
                idempotency_key=idempotency_key,
            )
        except JobConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        response = dict(record)
        response["created"] = created
        return response

    @app.post("/v1/agents/validate")
    def validate(request: ManifestRequest) -> dict[str, Any]:
        errors = validate_manifest(request.manifest)
        return {"valid": not errors, "errors": errors}

    @app.post("/v1/agents", status_code=status.HTTP_201_CREATED)
    def create_agent(
        request: ManifestRequest,
        principal: Principal = AUTHENTICATED_PRINCIPAL,
    ) -> dict[str, Any]:
        enforce_authorization(app, principal, "agent.registry", "agent.create", request.manifest.get("agent_id", ""))
        errors = validate_manifest(request.manifest)
        if errors:
            raise HTTPException(
                status_code=422,
                detail={"message": "Manifest validation failed", "errors": errors},
            )
        try:
            record, created = app.state.registry.create(request.manifest, tenant_id=principal.tenant_id)
        except VersionConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        response = record_response(record)
        response["created"] = created
        return response

    @app.get("/v1/agents/{agent_id}/versions")
    def list_versions(
        agent_id: str,
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        principal: Principal = AUTHENTICATED_PRINCIPAL,
    ) -> dict[str, Any]:
        enforce_authorization(app, principal, "agent.registry", "agent.read", agent_id)
        records = app.state.registry.list(agent_id, limit=limit, offset=offset, tenant_id=principal.tenant_id)
        return {
            "agent_id": agent_id,
            "items": [record_response(item) for item in records],
            "limit": limit,
            "offset": offset,
        }

    @app.get("/v1/agents/{agent_id}/versions/{version}")
    def get_version(
        agent_id: str, version: str,
        principal: Principal = AUTHENTICATED_PRINCIPAL,
    ) -> dict[str, Any]:
        enforce_authorization(app, principal, "agent.registry", "agent.read", agent_id)
        try:
            return record_response(app.state.registry.get(agent_id, version, principal.tenant_id))
        except AgentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(
        "/v1/agents/{agent_id}/versions/{version}/clone",
        status_code=status.HTTP_201_CREATED,
    )
    def clone_version(
        agent_id: str, version: str, request: CloneRequest,
        principal: Principal = AUTHENTICATED_PRINCIPAL,
    ) -> dict[str, Any]:
        enforce_authorization(app, principal, "agent.registry", "agent.clone", agent_id)
        try:
            source = app.state.registry.get(agent_id, version, principal.tenant_id)
        except AgentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        candidate = copy.deepcopy(source.manifest)
        forbidden = sorted(set(request.overrides) - MUTABLE_MANIFEST_FIELDS)
        if forbidden:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "Clone overrides contain immutable or unknown fields",
                    "fields": forbidden,
                },
            )
        candidate["version"] = request.new_version
        candidate = merge_overrides(candidate, request.overrides)
        candidate["provenance"] = copy.deepcopy(candidate["provenance"])
        candidate["provenance"]["origin"] = "derived"
        parent_ref = f"{agent_id}@{version}"
        source_refs = candidate["provenance"].setdefault("source_refs", [])
        if parent_ref not in source_refs:
            source_refs.append(parent_ref)
        errors = validate_manifest(candidate)
        if errors:
            raise HTTPException(
                status_code=422,
                detail={"message": "Cloned manifest is invalid", "errors": errors},
            )
        try:
            record, created = app.state.registry.create(
                candidate, parent_artifact_id=parent_ref, tenant_id=principal.tenant_id
            )
        except VersionConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        response = record_response(record)
        response["created"] = created
        return response

    @app.post(
        "/v1/agents/{agent_id}/versions/{version}/candidates",
        status_code=status.HTTP_201_CREATED,
    )
    def generate_agent_candidate(
        agent_id: str,
        version: str,
        request: CandidateGenerationRequest,
        principal: Principal = AUTHENTICATED_PRINCIPAL,
    ) -> dict[str, Any]:
        enforce_authorization(app, principal, "agent.registry", "agent.clone", agent_id)
        try:
            result = app.state.candidate_generation.generate(
                agent_id,
                version,
                request.candidate_version,
                request.operator_ids,
                request.seed,
                principal.tenant_id,
                principal.subject_id,
            )
        except AgentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except CandidateConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except CandidateValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        response = record_response(result["record"])
        response.update({key: value for key, value in result.items() if key != "record"})
        return response

    @app.post(
        "/v1/agents/{agent_id}/versions/{version}/executions",
        status_code=status.HTTP_201_CREATED,
    )
    def execute_agent(
        agent_id: str, version: str, request: ExecutionRequest,
        idempotency_key: str = Header(alias="Idempotency-Key"),
        principal: Principal = AUTHENTICATED_PRINCIPAL,
    ) -> dict[str, Any]:
        enforce_authorization(app, principal, "agent.execution", "agent.execute", agent_id)
        try:
            return app.state.executions.execute(
                agent_id, version, request.input, idempotency_key, request.max_output_tokens, principal.tenant_id
            )
        except AgentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ExecutionPolicyError as exc:
            code = 503 if "Provider is not configured" in str(exc) else 403
            raise HTTPException(status_code=code, detail=str(exc)) from exc
        except ExecutionConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post(
        "/v1/agents/{agent_id}/versions/{version}/jobs",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def submit_execution_job(
        agent_id: str,
        version: str,
        request: ExecutionJobRequest,
        idempotency_key: str = Header(alias="Idempotency-Key"),
        principal: Principal = AUTHENTICATED_PRINCIPAL,
    ) -> dict[str, Any]:
        enforce_authorization(app, principal, "agent.execution", "agent.execute", agent_id)
        try:
            artifact = app.state.registry.get(agent_id, version, principal.tenant_id)
        except AgentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        model_binding = artifact.manifest.get("model_binding", {})
        handler_id = model_binding.get("provider_id") if isinstance(model_binding, dict) else None
        handlers = [
            entry for entry in app.state.job_handler_allowlist
            if entry[0] == "provider_invocation" and entry[1] == handler_id
        ]
        if len(handlers) != 1:
            raise HTTPException(
                status_code=503,
                detail="No unique trusted durable-job handler is configured for this provider",
            )
        workload_kind, handler_id, handler_version = handlers[0]
        try:
            record, created = app.state.execution_jobs.create(
                tenant_id=principal.tenant_id,
                subject_id=principal.subject_id,
                principal_type=principal.principal_type,
                workload_kind=workload_kind,
                handler_id=handler_id,
                handler_version=handler_version,
                workload_ref=f"{agent_id}@{version}",
                workload_hash=artifact.content_hash,
                payload={"input": request.input, "max_output_tokens": request.max_output_tokens},
                idempotency_key=idempotency_key,
            )
        except JobConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        response = dict(record)
        response["created"] = created
        return response

    @app.get("/v1/execution-jobs/{job_id}")
    def get_execution_job(
        job_id: str,
        principal: Principal = AUTHENTICATED_PRINCIPAL,
    ) -> dict[str, Any]:
        try:
            record = app.state.execution_jobs.get(job_id, principal.tenant_id)
        except JobNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Execution job not found") from exc
        agent_id = record["workload_ref"].split("@", 1)[0]
        enforce_authorization(app, principal, "agent.execution", "execution.read", agent_id)
        return record

    @app.get("/v1/execution-jobs/{job_id}/events")
    def list_execution_job_events(
        job_id: str,
        principal: Principal = AUTHENTICATED_PRINCIPAL,
    ) -> dict[str, Any]:
        try:
            record = app.state.execution_jobs.get(job_id, principal.tenant_id)
        except JobNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Execution job not found") from exc
        agent_id = record["workload_ref"].split("@", 1)[0]
        enforce_authorization(app, principal, "agent.execution", "execution.read", agent_id)
        return {"items": app.state.execution_jobs.list_events(job_id, principal.tenant_id)}

    @app.post("/v1/execution-jobs/{job_id}/cancel")
    def cancel_execution_job(
        job_id: str,
        principal: Principal = AUTHENTICATED_PRINCIPAL,
    ) -> dict[str, Any]:
        try:
            record = app.state.execution_jobs.get(job_id, principal.tenant_id)
        except JobNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Execution job not found") from exc
        agent_id = record["workload_ref"].split("@", 1)[0]
        enforce_authorization(app, principal, "agent.execution", "agent.execute", agent_id)
        try:
            return app.state.execution_jobs.request_cancel(
                job_id, principal.tenant_id, principal.subject_id
            )
        except JobNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Execution job not found") from exc

    @app.post("/internal/v1/execution-jobs/claim")
    def claim_execution_job(
        request: WorkerClaimRequest,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> dict[str, Any]:
        require_job_worker(authorization)
        return {"job": app.state.execution_jobs.claim(request.worker_id, request.lease_seconds)}

    @app.post("/internal/v1/execution-jobs/recover")
    def recover_execution_jobs(
        request: WorkerRecoveryRequest,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> dict[str, int]:
        require_job_worker(authorization)
        return app.state.execution_jobs.recover_expired_leases(request.supervisor_id)

    @app.post("/internal/v1/execution-jobs/{job_id}/start")
    def start_execution_job(
        job_id: str,
        request: WorkerLeaseRequest,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> dict[str, Any]:
        require_job_worker(authorization)
        try:
            return app.state.execution_jobs.start(
                job_id, request.tenant_id, request.worker_id, request.lease_token
            )
        except JobNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Execution job not found") from exc
        except JobLeaseError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/internal/v1/execution-jobs/{job_id}/heartbeat")
    def heartbeat_execution_job(
        job_id: str,
        request: WorkerLeaseRequest,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> dict[str, Any]:
        require_job_worker(authorization)
        try:
            expiry = app.state.execution_jobs.heartbeat(
                job_id, request.tenant_id, request.worker_id, request.lease_token
            )
            return {"lease_expires_at": expiry}
        except JobNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Execution job not found") from exc
        except JobLeaseError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/internal/v1/execution-jobs/{job_id}/cancelled")
    def confirm_execution_job_cancel(
        job_id: str,
        request: WorkerCancelConfirmRequest,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> dict[str, Any]:
        require_job_worker(authorization)
        try:
            return app.state.execution_jobs.confirm_cancel(
                job_id, request.tenant_id, request.actor_id,
                request.worker_id, request.lease_token,
            )
        except JobNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Execution job not found") from exc
        except JobLeaseError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except JobConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/internal/v1/execution-jobs/{job_id}/complete")
    def complete_execution_job(
        job_id: str,
        request: WorkerCompleteRequest,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> dict[str, Any]:
        require_job_worker(authorization)
        try:
            return app.state.execution_jobs.complete(
                job_id, request.tenant_id, request.worker_id,
                request.lease_token, request.result,
            )
        except JobNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Execution job not found") from exc
        except JobLeaseError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/internal/v1/execution-jobs/{job_id}/fail")
    def fail_execution_job(
        job_id: str,
        request: WorkerFailRequest,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> dict[str, Any]:
        require_job_worker(authorization)
        try:
            return app.state.execution_jobs.fail(
                job_id, request.tenant_id, request.worker_id,
                request.lease_token, request.error_code,
            )
        except JobNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Execution job not found") from exc
        except JobLeaseError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/v1/executions/{execution_id}")
    def get_execution(
        execution_id: str,
        principal: Principal = AUTHENTICATED_PRINCIPAL,
    ) -> dict[str, Any]:
        try:
            record = app.state.executions.get(execution_id, principal.tenant_id)
            enforce_authorization(app, principal, "agent.execution", "execution.read", record["agent_id"])
            return record
        except ExecutionNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/v1/evaluation-profiles/validate")
    def validate_profile(request: EvaluationProfileRequest) -> dict[str, Any]:
        errors = validate_evaluation_profile(request.profile)
        return {"valid": not errors, "errors": errors}

    @app.post("/v1/evaluation-profiles", status_code=status.HTTP_201_CREATED)
    def create_profile(
        request: EvaluationProfileRequest,
        principal: Principal = AUTHENTICATED_PRINCIPAL,
    ) -> dict[str, Any]:
        enforce_authorization(app, principal, "evaluation.profile", "profile.create", request.profile.get("profile_id", ""))
        errors = validate_evaluation_profile(request.profile)
        if errors:
            raise HTTPException(
                status_code=422,
                detail={"message": "Evaluation profile validation failed", "errors": errors},
            )
        try:
            record, created = app.state.evaluations.create_profile(request.profile, principal.tenant_id)
        except EvaluationProfileConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        record["created"] = created
        return record

    @app.get("/v1/evaluation-profiles/{profile_id}/versions")
    def list_profile_versions(
        profile_id: str,
        principal: Principal = AUTHENTICATED_PRINCIPAL,
    ) -> dict[str, Any]:
        enforce_authorization(app, principal, "evaluation.profile", "profile.read", profile_id)
        records = app.state.evaluations.list_profile_versions(profile_id, principal.tenant_id)
        return {"profile_id": profile_id, "items": records}

    @app.get("/v1/evaluation-profiles/{profile_id}/versions/{profile_version}")
    def get_profile(
        profile_id: str, profile_version: str,
        principal: Principal = AUTHENTICATED_PRINCIPAL,
    ) -> dict[str, Any]:
        enforce_authorization(app, principal, "evaluation.profile", "profile.read", profile_id)
        try:
            return app.state.evaluations.get_profile(profile_id, profile_version, principal.tenant_id)
        except EvaluationProfileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(
        "/v1/agents/{agent_id}/versions/{version}/evaluations",
        status_code=status.HTTP_201_CREATED,
    )
    def evaluate_agent_version(
        agent_id: str, version: str, request: EvaluationRunRequest,
        principal: Principal = AUTHENTICATED_PRINCIPAL,
    ) -> dict[str, Any]:
        enforce_authorization(app, principal, "agent.evaluation", "evaluation.create", agent_id)
        enforce_authorization(
            app, principal, "evaluation.profile", "profile.read", request.profile_id
        )
        try:
            app.state.registry.get(agent_id, version, principal.tenant_id)
        except AgentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        try:
            return app.state.evaluations.create_run(
                agent_id,
                version,
                request.profile_id,
                request.profile_version,
                [item.model_dump() for item in request.results],
                principal.tenant_id,
            )
        except EvaluationProfileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post(
        "/v1/agents/{agent_id}/versions/{version}/evaluations/execute",
        status_code=status.HTTP_201_CREATED,
    )
    def execute_agent_evaluation(
        agent_id: str,
        version: str,
        request: EvaluationExecutionRequest,
        idempotency_key: str = Header(alias="Idempotency-Key"),
        principal: Principal = AUTHENTICATED_PRINCIPAL,
    ) -> dict[str, Any]:
        """Run pinned profile cases through the configured provider and score recorded outputs."""
        if not idempotency_key.strip() or len(idempotency_key) > 128:
            raise HTTPException(
                status_code=422,
                detail="Idempotency-Key must contain 1 to 128 characters",
            )
        enforce_authorization(app, principal, "agent.execution", "agent.execute", agent_id)
        enforce_authorization(app, principal, "agent.evaluation", "evaluation.create", agent_id)
        enforce_authorization(
            app, principal, "evaluation.profile", "profile.read", request.profile_id
        )
        try:
            artifact = app.state.registry.get(agent_id, version, principal.tenant_id)
            profile_record = app.state.evaluations.get_profile(
                request.profile_id, request.profile_version, principal.tenant_id
            )
        except AgentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except EvaluationProfileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        profile = profile_record["profile"]
        cases = profile.get("cases", [])
        if not isinstance(cases, list) or not 1 <= len(cases) <= 100:
            raise HTTPException(
                status_code=422,
                detail="Execution-backed evaluations require 1 to 100 cases",
            )
        if artifact.manifest.get("tools"):
            raise HTTPException(
                status_code=403,
                detail=(
                    "Tool-enabled agents cannot be evaluated until trusted tool execution "
                    "is available"
                ),
            )
        if any(not isinstance(case, dict) or "input" not in case for case in cases):
            raise HTTPException(
                status_code=422, detail="Every evaluation case must define an input"
            )

        workload_definition = {
            "profile_id": request.profile_id,
            "profile_version": request.profile_version,
            "profile_content_hash": profile_record["content_hash"],
            "cases": [{"case_id": case["case_id"], "input": case["input"]} for case in cases],
        }
        workload_hash = hashlib.sha256(
            json.dumps(workload_definition, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False, allow_nan=False).encode()
        ).hexdigest()
        submitted_results: list[dict[str, Any]] = []
        case_evidence: list[dict[str, Any]] = []
        for case in cases:
            case_id = case["case_id"]
            input_hash = hashlib.sha256(
                json.dumps({"input": case["input"]}, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False, allow_nan=False).encode("utf-8")
            ).hexdigest()
            case_key = hashlib.sha256(
                f"{idempotency_key}:{principal.tenant_id}:{agent_id}:{artifact.content_hash}:"
                f"{profile_record['content_hash']}:{case_id}".encode("utf-8")
            ).hexdigest()
            try:
                execution = app.state.executions.execute(
                    agent_id,
                    version,
                    case["input"],
                    case_key,
                    request.max_output_tokens,
                    principal.tenant_id,
                )
            except ExecutionPolicyError as exc:
                code = 503 if "Provider is not configured" in str(exc) else 403
                raise HTTPException(status_code=code, detail=str(exc)) from exc
            except ExecutionConflictError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except ProviderError as exc:
                raise HTTPException(
                    status_code=502, detail="Configured provider execution failed"
                ) from exc
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            if execution["status"] != "completed" or not isinstance(execution.get("result"), dict):
                raise HTTPException(
                    status_code=502,
                    detail="Provider execution did not complete successfully",
                )
            executed = execution["result"]
            usage = executed.get("usage", {})
            cost = usage.get("cost_usd") if isinstance(usage, dict) else None
            latency = executed.get("latency_ms")
            if cost is None or latency is None:
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "Provider execution lacks recorded cost or latency; "
                        "evaluation fails closed"
                    ),
                )
            submitted_results.append({
                "case_id": case_id,
                "output": executed.get("output"),
                "latency_ms": latency,
                "cost_usd": cost,
            })
            case_evidence.append({
                "case_id": case_id,
                "execution_id": execution["execution_id"],
                "input_hash": input_hash,
                "output_hash": hashlib.sha256(
                    json.dumps(
                        {"output": executed.get("output")}, sort_keys=True,
                        separators=(",", ":"), ensure_ascii=False, allow_nan=False,
                    ).encode("utf-8")
                ).hexdigest(),
                "provider_id": executed.get("provider_id"),
                "model_id": executed.get("model_id"),
                "latency_ms": latency,
                "cost_usd": cost,
            })

        execution_evidence = {
            "source": "server_execution_records",
            "agent_content_hash": artifact.content_hash,
            "profile_content_hash": profile_record["content_hash"],
            "workload_hash": workload_hash,
            "case_executions": case_evidence,
        }
        try:
            return app.state.evaluations.create_run(
                agent_id,
                version,
                request.profile_id,
                request.profile_version,
                submitted_results,
                principal.tenant_id,
                execution_evidence=execution_evidence,
            )
        except EvaluationProfileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/v1/evaluations/{run_id}")
    def get_evaluation_run(
        run_id: str,
        principal: Principal = AUTHENTICATED_PRINCIPAL,
    ) -> dict[str, Any]:
        try:
            record = app.state.evaluations.get_run(run_id, principal.tenant_id)
            enforce_authorization(app, principal, "agent.evaluation", "evaluation.read", record["agent_id"])
            return record
        except EvaluationRunNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/v1/agents/{agent_id}/versions/{version}/evaluations")
    def list_evaluations(
        agent_id: str, version: str,
        principal: Principal = AUTHENTICATED_PRINCIPAL,
    ) -> dict[str, Any]:
        enforce_authorization(app, principal, "agent.evaluation", "evaluation.read", agent_id)
        try:
            app.state.registry.get(agent_id, version, principal.tenant_id)
        except AgentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        runs = app.state.evaluations.list_agent_runs(agent_id, version, principal.tenant_id)
        return {"agent_id": agent_id, "agent_version": version, "items": runs}



    @app.post("/v1/agent-experiences", status_code=status.HTTP_201_CREATED)
    def create_agent_experience(
        request: ExperienceRecordRequest,
        principal: Principal = AUTHENTICATED_PRINCIPAL,
    ) -> dict[str, Any]:
        enforce_authorization(
            app, principal, "agent.experience", "experience.create", "tenant-experience"
        )
        try:
            record, created = app.state.experiences.record(
                request.execution_id, request.evaluation_run_id, request.workload_ref,
                request.workload_hash, principal.tenant_id, principal.subject_id,
            )
            response = dict(record)
            response["created"] = created
            return response
        except ExperienceNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ExperienceConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ExperienceValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/v1/agents/{agent_id}/experiences")
    def list_agent_experiences(
        agent_id: str,
        profile_id: str | None = None,
        profile_version: str | None = None,
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        principal: Principal = AUTHENTICATED_PRINCIPAL,
    ) -> dict[str, Any]:
        enforce_authorization(app, principal, "agent.experience", "experience.read", agent_id)
        try:
            items = app.state.experiences.list_for_agent(
                agent_id, principal.tenant_id, profile_id, profile_version, limit, offset
            )
            return {"agent_id": agent_id, "items": items, "limit": limit, "offset": offset}
        except ExperienceValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/v1/agent-experiences/{experience_id}/lessons", status_code=status.HTTP_201_CREATED)
    def create_learning_lesson(
        experience_id: str,
        request: LearningLessonRequest,
        principal: Principal = AUTHENTICATED_PRINCIPAL,
    ) -> dict[str, Any]:
        try:
            experience = app.state.experiences.get(experience_id, principal.tenant_id)
            enforce_authorization(
                app, principal, "agent.experience", "lesson.create", experience["agent_id"]
            )
            record, created = app.state.experiences.create_lesson(
                experience_id, request.lesson_type, request.statement,
                request.evidence_refs, request.source_kind, request.extractor_version,
                request.confidence, principal.tenant_id, principal.subject_id,
            )
            response = dict(record)
            response["created"] = created
            return response
        except ExperienceNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ExperienceValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/v1/agent-experiences/{experience_id}/lessons")
    def list_learning_lessons(
        experience_id: str,
        limit: int = Query(default=50, ge=1, le=100),
        principal: Principal = AUTHENTICATED_PRINCIPAL,
    ) -> dict[str, Any]:
        try:
            experience = app.state.experiences.get(experience_id, principal.tenant_id)
            enforce_authorization(
                app, principal, "agent.experience", "lesson.read", experience["agent_id"]
            )
            enforce_authorization(
                app, principal, "agent.experience", "experience.read", experience["agent_id"]
            )
            return {"items": app.state.experiences.list_lessons(
                experience_id, principal.tenant_id, limit
            )}
        except ExperienceNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ExperienceValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/v1/cognitive-runs", status_code=status.HTTP_201_CREATED)
    def create_cognitive_run(
        request: CognitiveRunRequest,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=256),
        principal: Principal = AUTHENTICATED_PRINCIPAL,
    ) -> dict[str, Any]:
        if "@" not in request.agent_ref:
            raise HTTPException(status_code=422, detail="agent_ref must identify an immutable agent version")
        agent_id, agent_version = request.agent_ref.rsplit("@", 1)
        if not agent_id or not agent_version:
            raise HTTPException(status_code=422, detail="agent_ref must identify an immutable agent version")
        enforce_authorization(
            app, principal, "agent.cognition", "cognition.run.create", agent_id
        )
        try:
            agent = app.state.registry.get(agent_id, agent_version, principal.tenant_id)
            run, event, created = app.state.cognitive_runs.create_run(
                request.goal_ref, request.agent_ref, agent.agent_id, agent.version,
                agent.content_hash, principal.tenant_id, principal.subject_id, idempotency_key,
            )
            return {"run": run, "initial_event": event, "created": created}
        except AgentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except CognitiveRunConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except CognitiveRunValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/v1/cognitive-runs/{run_id}")
    def get_cognitive_run(
        run_id: str,
        principal: Principal = AUTHENTICATED_PRINCIPAL,
    ) -> dict[str, Any]:
        try:
            run = app.state.cognitive_runs.get_run(run_id, principal.tenant_id)
            enforce_authorization(
                app, principal, "agent.cognition", "cognition.run.read", run["agent_id"]
            )
            return run
        except CognitiveRunNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/v1/cognitive-runs/{run_id}/events")
    def list_cognitive_run_events(
        run_id: str,
        after_sequence: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=200),
        principal: Principal = AUTHENTICATED_PRINCIPAL,
    ) -> dict[str, Any]:
        try:
            run = app.state.cognitive_runs.get_run(run_id, principal.tenant_id)
            enforce_authorization(
                app, principal, "agent.cognition", "cognition.run.read", run["agent_id"]
            )
            items = app.state.cognitive_runs.list_events(
                run_id, principal.tenant_id, after_sequence, limit
            )
            return {"run_id": run_id, "items": items, "limit": limit, "after_sequence": after_sequence}
        except CognitiveRunNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except CognitiveRunValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/v1/cognitive-runs/{run_id}/events", status_code=status.HTTP_201_CREATED)
    def append_cognitive_run_event(
        run_id: str,
        request: CognitiveEventRequest,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=256),
        principal: Principal = AUTHENTICATED_PRINCIPAL,
    ) -> dict[str, Any]:
        try:
            run = app.state.cognitive_runs.get_run(run_id, principal.tenant_id)
            enforce_authorization(
                app, principal, "agent.cognition", "cognition.event.append", run["agent_id"]
            )
            event, created = app.state.cognitive_runs.append_event(
                run_id, request.model_dump(exclude_none=True), principal.tenant_id,
                principal.subject_id, idempotency_key,
            )
            return {"event": event, "created": created}
        except CognitiveRunNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except CognitiveRunConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except CognitiveRunValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/v1/cognitive-runs/{run_id}/context", status_code=status.HTTP_201_CREATED)
    def assemble_cognitive_context(
        run_id: str,
        request: CognitiveContextRequest,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=256),
        principal: Principal = AUTHENTICATED_PRINCIPAL,
    ) -> dict[str, Any]:
        try:
            run = app.state.cognitive_runs.get_run(run_id, principal.tenant_id)
            enforce_authorization(
                app, principal, "agent.cognition", "cognition.context.assemble", run["agent_id"]
            )
            enforce_authorization(
                app, principal, "enterprise.context", "context.entity.read", "tenant-context"
            )
            enforce_authorization(
                app, principal, "enterprise.context", "context.relationship.read", "tenant-context"
            )
            enforce_authorization(
                app, principal, "enterprise.context", "context.evidence.read", "tenant-context"
            )
            if request.entity_ids is not None and request.search is not None:
                raise HTTPException(status_code=422, detail="Specify entity_ids or search, not both")
            assembled = app.state.context_assembly.assemble(
                principal.tenant_id,
                entity_ids=request.entity_ids,
                search=request.search,
                include_inferences=request.include_inferences,
                limit=request.limit,
            )
            packet = assembled["packet"]
            event_input = {
                "stage": "context",
                "event_type": "context.assembled",
                "evidence_status": "unverified",
                "input_refs": [item["entity_id"] for item in packet["entities"]],
                "output_refs": [assembled["snapshot_ref"]],
                "evidence_refs": [item["evidence_id"] for item in packet["evidence"]],
                "payload": {
                    "context_snapshot_ref": assembled["snapshot_ref"],
                    "context_content_hash": assembled["content_hash"],
                    "context_snapshot": packet,
                },
            }
            event, created = app.state.cognitive_runs.append_event(
                run_id, event_input, principal.tenant_id, principal.subject_id, idempotency_key
            )
            return {
                "snapshot_ref": assembled["snapshot_ref"],
                "content_hash": assembled["content_hash"],
                "byte_length": assembled["byte_length"],
                "snapshot": packet,
                "event": event,
                "created": created,
            }
        except ContextAssemblyValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ContextNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ContextValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except CognitiveRunNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except CognitiveRunConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except CognitiveRunValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/v1/agent-experiment-comparisons", status_code=status.HTTP_201_CREATED)
    def create_agent_experiment_comparison(
        request: ExperimentComparisonRequest,
        principal: Principal = AUTHENTICATED_PRINCIPAL,
    ) -> dict[str, Any]:
        enforce_authorization(
            app, principal, "agent.experiment", "experiment.compare", request.agent_id
        )
        try:
            record, created = app.state.experiment_comparisons.compare(
                agent_id=request.agent_id,
                parent_version=request.parent_version,
                candidate_version=request.candidate_version,
                parent_evaluation_run_id=request.parent_evaluation_run_id,
                candidate_evaluation_run_id=request.candidate_evaluation_run_id,
                workload_ref=request.workload_ref,
                workload_hash=request.workload_hash,
                tenant_id=principal.tenant_id,
                actor_id=principal.subject_id,
            )
            response = dict(record)
            response["created"] = created
            return response
        except ExperimentComparisonNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ExperimentComparisonConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ExperimentComparisonValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/v1/agent-experiment-comparisons/{comparison_id}")
    def get_agent_experiment_comparison(
        comparison_id: str,
        principal: Principal = AUTHENTICATED_PRINCIPAL,
    ) -> dict[str, Any]:
        try:
            record = app.state.experiment_comparisons.get(comparison_id, principal.tenant_id)
            enforce_authorization(
                app, principal, "agent.experiment", "experiment.read", record["agent_id"]
            )
            return record
        except ExperimentComparisonNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/v1/fitness/selection")
    def select_fitness_candidates(
        profile_id: str,
        profile_version: str,
        min_samples: int = Query(default=3, ge=1, le=1000),
        limit: int = Query(default=50, ge=1, le=100),
        principal: Principal = AUTHENTICATED_PRINCIPAL,
    ) -> dict[str, Any]:
        enforce_authorization(
            app, principal, "agent.experience", "fitness.select", profile_id
        )
        try:
            items = app.state.experiences.select_fitness(
                profile_id, profile_version, principal.tenant_id, min_samples, limit
            )
            return {
                "profile_id": profile_id, "profile_version": profile_version,
                "min_samples": min_samples, "items": items,
            }
        except ExperienceNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ExperienceValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post(
        "/v1/enterprise-discovery/postgresql/connectors",
        status_code=status.HTTP_201_CREATED,
    )
    def register_postgresql_connector(
        request: PostgreSQLConnectorRegistrationRequest,
        principal: Principal = AUTHENTICATED_PRINCIPAL,
    ) -> dict[str, Any]:
        enforce_authorization(
            app, principal, "enterprise.discovery", "connector.register",
            request.connector_id,
        )
        try:
            return app.state.postgresql_connectors.register(
                principal.tenant_id, request.connector_id, request.source_ref,
                request.key_id, request.public_key, principal.subject_id,
            )
        except ContextConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ContextValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/v1/enterprise-discovery/postgresql/connectors")
    def list_postgresql_connectors(
        principal: Principal = AUTHENTICATED_PRINCIPAL,
    ) -> dict[str, Any]:
        enforce_authorization(
            app, principal, "enterprise.discovery", "connector.read", "tenant-connectors"
        )
        return {"items": app.state.postgresql_connectors.list_connectors(principal.tenant_id)}

    @app.post(
        "/v1/enterprise-discovery/postgresql/connectors/{connector_id}/keys",
        status_code=status.HTTP_201_CREATED,
    )
    def rotate_postgresql_connector_key(
        connector_id: str,
        request: PostgreSQLConnectorKeyRequest,
        principal: Principal = AUTHENTICATED_PRINCIPAL,
    ) -> dict[str, Any]:
        enforce_authorization(
            app, principal, "enterprise.discovery", "connector.rotate_key", connector_id
        )
        try:
            return app.state.postgresql_connectors.add_key(
                principal.tenant_id, connector_id, request.key_id,
                request.public_key, principal.subject_id,
            )
        except ContextNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ContextConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ContextValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post(
        "/v1/enterprise-discovery/postgresql/connectors/{connector_id}/keys/{key_id}/revoke",
    )
    def revoke_postgresql_connector_key(
        connector_id: str,
        key_id: str,
        principal: Principal = AUTHENTICATED_PRINCIPAL,
    ) -> dict[str, Any]:
        enforce_authorization(
            app, principal, "enterprise.discovery", "connector.revoke_key", connector_id
        )
        try:
            return app.state.postgresql_connectors.revoke_key(
                principal.tenant_id, connector_id, key_id, principal.subject_id
            )
        except ContextNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ContextConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ContextValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/v1/enterprise-discovery/postgresql/snapshots/ingest", status_code=status.HTTP_201_CREATED)
    def ingest_postgresql_snapshot(
        request: PostgreSQLSnapshotIngestRequest,
        principal: Principal = AUTHENTICATED_PRINCIPAL,
    ) -> dict[str, Any]:
        enforce_authorization(
            app, principal, "enterprise.discovery", "discovery.snapshot.ingest", "tenant-context"
        )
        try:
            normalized_source_ref = PostgreSQLSnapshotIngestion._validate_source_ref(
                request.source_ref
            )
            provenance = None
            if request.attestation is not None:
                if request.connector_id is None:
                    raise ContextValidationError(
                        "connector_id is required for signed connector snapshots"
                    )
                key = app.state.postgresql_connectors.get_active_key(
                    principal.tenant_id, request.connector_id,
                    request.attestation.key_id, normalized_source_ref,
                )
                verifier = PostgreSQLProvenanceVerifier(
                    {request.attestation.key_id: key}
                )
                provenance = verifier.verify(
                    request.attestation.model_dump(), request.snapshot,
                    principal.tenant_id, normalized_source_ref,
                )
                provenance["connector_id"] = request.connector_id
            return app.state.postgresql_ingestion.ingest(
                request.snapshot, principal.tenant_id, principal.subject_id,
                normalized_source_ref, provenance,
            )
        except ContextNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ContextConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ContextValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/v1/enterprise-context/entities", status_code=status.HTTP_201_CREATED)
    def create_context_entity(
        request: ContextEntityRequest,
        principal: Principal = AUTHENTICATED_PRINCIPAL,
    ) -> dict[str, Any]:
        enforce_authorization(
            app, principal, "enterprise.context", "context.entity.create", request.entity_id
        )
        if request.assertion_class == "human_validated":
            enforce_authorization(
                app, principal, "enterprise.context", "context.entity.validate", request.entity_id
            )
        try:
            return app.state.enterprise_context.create_entity(
                request.model_dump(exclude_none=True), principal.tenant_id, principal.subject_id
            )
        except ContextConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ContextValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post(
        "/v1/enterprise-context/entities/{entity_id}/versions",
        status_code=status.HTTP_201_CREATED,
    )
    def version_context_entity(
        entity_id: str,
        request: ContextEntityVersionRequest,
        principal: Principal = AUTHENTICATED_PRINCIPAL,
    ) -> dict[str, Any]:
        enforce_authorization(
            app, principal, "enterprise.context", "context.entity.update", entity_id
        )
        if request.entity.assertion_class == "human_validated":
            enforce_authorization(
                app, principal, "enterprise.context", "context.entity.validate", entity_id
            )
        if request.entity.entity_id != entity_id:
            raise HTTPException(status_code=422, detail="Path entity_id must match entity payload")
        try:
            return app.state.enterprise_context.version_entity(
                request.entity.model_dump(exclude_none=True),
                principal.tenant_id,
                principal.subject_id,
                request.expected_version,
            )
        except ContextNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ContextConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ContextValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/v1/enterprise-context/entities")
    def query_context_entities(
        entity_type: str | None = None,
        assertion_class: str | None = None,
        search: str | None = None,
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        principal: Principal = AUTHENTICATED_PRINCIPAL,
    ) -> dict[str, Any]:
        enforce_authorization(
            app, principal, "enterprise.context", "context.entity.read", "tenant-context"
        )
        try:
            items = app.state.enterprise_context.query_entities(
                principal.tenant_id, entity_type, assertion_class, search, limit, offset
            )
        except ContextValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"items": items, "limit": limit, "offset": offset}

    @app.get("/v1/enterprise-context/entities/{entity_id}")
    def get_context_entity(
        entity_id: str,
        version: int | None = Query(default=None, ge=1),
        principal: Principal = AUTHENTICATED_PRINCIPAL,
    ) -> dict[str, Any]:
        enforce_authorization(
            app, principal, "enterprise.context", "context.entity.read", entity_id
        )
        try:
            return app.state.enterprise_context.get_entity(
                entity_id, principal.tenant_id, version
            )
        except ContextNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/v1/enterprise-context/relationships", status_code=status.HTTP_201_CREATED)
    def create_context_relationship(
        request: ContextRelationshipRequest,
        principal: Principal = AUTHENTICATED_PRINCIPAL,
    ) -> dict[str, Any]:
        enforce_authorization(
            app, principal, "enterprise.context", "context.relationship.create",
            request.relationship_id,
        )
        if request.assertion_class == "human_validated":
            enforce_authorization(
                app, principal, "enterprise.context", "context.relationship.validate",
                request.relationship_id,
            )
        try:
            return app.state.enterprise_context.create_relationship(
                request.model_dump(exclude_none=True), principal.tenant_id, principal.subject_id
            )
        except ContextNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ContextConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ContextValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/v1/enterprise-context/relationships")
    def query_context_relationships(
        entity_id: str | None = None,
        relationship_type: str | None = None,
        limit: int = Query(default=100, ge=1, le=200),
        principal: Principal = AUTHENTICATED_PRINCIPAL,
    ) -> dict[str, Any]:
        resource_id = entity_id or "tenant-context"
        enforce_authorization(
            app, principal, "enterprise.context", "context.relationship.read", resource_id
        )
        try:
            items = app.state.enterprise_context.query_relationships(
                principal.tenant_id, entity_id, relationship_type, limit
            )
        except ContextValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"items": items}

    @app.post("/v1/enterprise-context/evidence", status_code=status.HTTP_201_CREATED)
    def create_context_evidence(
        request: ContextEvidenceRequest,
        principal: Principal = AUTHENTICATED_PRINCIPAL,
    ) -> dict[str, Any]:
        enforce_authorization(
            app, principal, "enterprise.context", "context.evidence.create", request.entity_id
        )
        try:
            return app.state.enterprise_context.create_evidence(
                request.model_dump(exclude_none=True),
                principal.tenant_id,
                principal.subject_id,
            )
        except ContextNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ContextConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ContextValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post(
        "/v1/enterprise-context/evidence/{evidence_id}/reviews",
        status_code=status.HTTP_201_CREATED,
    )
    def review_context_evidence(
        evidence_id: str,
        request: ContextEvidenceReviewRequest,
        principal: Principal = AUTHENTICATED_PRINCIPAL,
    ) -> dict[str, Any]:
        enforce_authorization(
            app, principal, "enterprise.context", "context.evidence.review", evidence_id
        )
        try:
            return app.state.enterprise_context.review_evidence(
                evidence_id, principal.tenant_id, principal.subject_id,
                request.decision, request.reason, request.confirmed_source_hash,
            )
        except ContextNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ContextConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ContextValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/v1/enterprise-context/entities/{entity_id}/evidence")
    def query_context_evidence(
        entity_id: str,
        limit: int = Query(default=100, ge=1, le=200),
        principal: Principal = AUTHENTICATED_PRINCIPAL,
    ) -> dict[str, Any]:
        enforce_authorization(
            app, principal, "enterprise.context", "context.evidence.read", entity_id
        )
        try:
            return {
                "items": app.state.enterprise_context.query_evidence(
                    entity_id, principal.tenant_id, limit
                )
            }
        except ContextValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/v1/enterprise-context/events")
    def query_context_events(
        since: str | None = None,
        limit: int = Query(default=100, ge=1, le=200),
        principal: Principal = AUTHENTICATED_PRINCIPAL,
    ) -> dict[str, Any]:
        enforce_authorization(
            app, principal, "enterprise.context", "context.event.read", "tenant-context"
        )
        try:
            return {
                "items": app.state.enterprise_context.query_events(
                    principal.tenant_id, since, limit
                )
            }
        except ContextValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return app


app = create_app()

"""Typed validation for the AgentBox cognitive lifecycle event envelope.

Events are evidence records, not commands. This module does not execute plans,
invoke capabilities, grant permissions, or write memory.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError


def _load_validator() -> Draft202012Validator:
    configured = os.getenv("AGENTBOX_COGNITIVE_EVENT_SCHEMA_PATH")
    path = Path(configured) if configured else (
        Path(__file__).resolve().parents[2] / "schemas" / "cognitive-event-v1.schema.json"
    )
    if not path.is_file():
        raise RuntimeError(f"AgentBox cognitive event schema not found: {path}")
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def validate_cognitive_event(event: dict[str, Any]) -> list[dict[str, str]]:
    """Return deterministic schema and evidence/authority invariant errors."""
    if not isinstance(event, dict):
        return [{"path": "/", "message": "Cognitive event must be an object."}]
    try:
        validator = _load_validator()
    except (OSError, json.JSONDecodeError, SchemaError) as exc:
        raise RuntimeError(f"Unable to load AgentBox cognitive event schema: {exc}") from exc

    errors = sorted(
        validator.iter_errors(event),
        key=lambda error: (list(map(str, error.absolute_path)), error.message),
    )
    result = [
        {
            "path": "/" + "/".join(str(part) for part in error.absolute_path),
            "message": error.message,
        }
        for error in errors
    ]
    if result:
        return result

    if type(event["sequence"]) is not int:
        result.append({
            "path": "/sequence",
            "message": "Event sequence must be an exact integer.",
        })

    event_type = event["event_type"]
    evidence_refs = event.get("evidence_refs", [])
    payload = event.get("payload", {})
    if (
        event_type in {"verification.passed", "verification.failed", "memory.accepted"}
        and (event["evidence_status"] != "verified" or not evidence_refs)
    ):
        result.append({
            "path": "/evidence_refs",
            "message": f"{event_type} requires verified evidence references.",
        })

    if event_type in {"responsibility.authorized", "responsibility.denied"}:
        decision_ref = payload.get("authorization_decision_ref")
        if not isinstance(decision_ref, str) or not decision_ref.strip():
            result.append({
                "path": "/payload/authorization_decision_ref",
                "message": "Responsibility decisions require an authorization decision reference.",
            })

    if event_type == "capability.selected":
        capability_ref = payload.get("capability_ref")
        if not isinstance(capability_ref, str) or not capability_ref.strip():
            result.append({
                "path": "/payload/capability_ref",
                "message": "Capability selection requires a versioned capability reference.",
            })

    return sorted(result, key=lambda item: (item["path"], item["message"]))


LIFECYCLE_STAGES = (
    "intent",
    "context",
    "cognition",
    "capability",
    "responsibility",
    "execution",
    "observation",
    "verification",
    "memory",
)


def validate_stage_transition(previous_stage: str, next_stage: str) -> list[dict[str, str]]:
    """Validate a forward-only lifecycle transition; repeats support same-stage work."""
    if previous_stage not in LIFECYCLE_STAGES:
        return [{"path": "/previous_stage", "message": "Unknown previous lifecycle stage."}]
    if next_stage not in LIFECYCLE_STAGES:
        return [{"path": "/next_stage", "message": "Unknown next lifecycle stage."}]
    previous_index = LIFECYCLE_STAGES.index(previous_stage)
    next_index = LIFECYCLE_STAGES.index(next_stage)
    if next_index < previous_index:
        return [{
            "path": "/next_stage",
            "message": "Lifecycle stages cannot move backwards within one run.",
        }]
    if next_index > previous_index + 1:
        return [{
            "path": "/next_stage",
            "message": "Lifecycle transitions cannot skip a stage.",
        }]
    return []

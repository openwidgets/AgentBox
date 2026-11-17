"""Capability-scoped secret broker contract.

This module defines the worker-to-broker boundary only. The default broker denies
all requests; deployments must supply an authenticated remote broker implementation.
Secret material is never stored in contracts, jobs, or audit records.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Protocol

_SECRET_REF = re.compile(r"^secret://[a-z0-9][a-z0-9._/-]{2,254}$")
_PURPOSE = re.compile(r"^[a-z][a-z0-9._:-]{2,127}$")


@dataclass(frozen=True)
class CapabilitySecretRequest:
    """Non-secret request identity evaluated by the broker."""

    tenant_id: str
    subject_id: str
    capability_id: str
    capability_version: str
    contract_hash: str
    secret_ref: str
    purpose: str


class CapabilitySecretBroker(Protocol):
    """Authenticated broker contract; implementations must fail closed."""

    def resolve(self, request: CapabilitySecretRequest) -> str:
        """Return short-lived secret material only after exact scope authorization."""
        ...


class DenyAllCapabilitySecretBroker:
    """Safe default used when no deployment broker has been configured."""

    def resolve(self, request: CapabilitySecretRequest) -> str:
        del request
        raise PermissionError("capability secret broker is not configured")


def validate_secret_request(secret_ref: str, purpose: str) -> None:
    """Reject malformed or unbounded secret references before contacting a broker."""
    if not isinstance(secret_ref, str) or not _SECRET_REF.fullmatch(secret_ref):
        raise ValueError("secret_ref must be an opaque secret:// reference")
    if not isinstance(purpose, str) or not _PURPOSE.fullmatch(purpose):
        raise ValueError("secret purpose is invalid")

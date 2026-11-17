"""Cryptographic provenance verification for PostgreSQL catalog snapshots.

Trusted public keys are provisioned out-of-band. The API never accepts public keys
or trust decisions from the snapshot submitter.
"""
from __future__ import annotations

import base64
import binascii
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .enterprise_context import ContextValidationError

MAX_ATTESTATION_AGE = timedelta(minutes=15)
MAX_FUTURE_SKEW = timedelta(seconds=60)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")


class PostgreSQLProvenanceVerifier:
    """Verify Ed25519 attestations against server-configured connector trust keys."""

    def __init__(self, trusted_keys: dict[str, bytes] | None = None):
        self._keys: dict[str, Ed25519PublicKey] = {}
        for key_id, raw_key in (trusted_keys or {}).items():
            if (
                not isinstance(key_id, str) or not key_id
                or len(key_id) > 128 or not isinstance(raw_key, bytes)
                or len(raw_key) != 32
            ):
                raise ValueError("Trusted provenance keys require key IDs and 32-byte Ed25519 public keys")
            self._keys[key_id] = Ed25519PublicKey.from_public_bytes(raw_key)

    @staticmethod
    def signing_payload(
        *, tenant_id: str, source_ref: str, snapshot: dict[str, Any],
        key_id: str, signed_at: str,
    ) -> bytes:
        return _canonical({
            "contract": "agentbox.postgresql-catalog-attestation.v1",
            "tenant_id": tenant_id,
            "source_ref": source_ref,
            "snapshot_hash": snapshot.get("snapshot_hash"),
            "discovered_at": snapshot.get("discovered_at"),
            "key_id": key_id,
            "signed_at": signed_at,
        })

    def verify(
        self, attestation: dict[str, Any], snapshot: dict[str, Any],
        tenant_id: str, source_ref: str,
    ) -> dict[str, str]:
        if not isinstance(attestation, dict) or set(attestation) != {
            "algorithm", "key_id", "signed_at", "signature"
        }:
            raise ContextValidationError("attestation must contain exactly algorithm, key_id, signed_at and signature")
        if attestation["algorithm"] != "Ed25519":
            raise ContextValidationError("Only Ed25519 connector attestations are supported")
        key_id = attestation["key_id"]
        if not isinstance(key_id, str) or not key_id or len(key_id) > 128:
            raise ContextValidationError("attestation key_id is invalid")
        key = self._keys.get(key_id)
        if key is None:
            raise ContextValidationError("attestation key is not trusted")
        signed_at = attestation["signed_at"]
        if not isinstance(signed_at, str):
            raise ContextValidationError("attestation signed_at must be an ISO timestamp")
        try:
            signed_time = datetime.fromisoformat(signed_at)
            if signed_time.tzinfo is None:
                raise ValueError
            signed_time = signed_time.astimezone(UTC)
            discovered = datetime.fromisoformat(snapshot["discovered_at"])
            if discovered.tzinfo is None:
                raise ValueError
            discovered = discovered.astimezone(UTC)
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise ContextValidationError("attestation and snapshot timestamps must be timezone-aware ISO timestamps") from exc
        now = datetime.now(UTC)
        if signed_time < now - MAX_ATTESTATION_AGE or signed_time > now + MAX_FUTURE_SKEW:
            raise ContextValidationError("attestation is outside the 15-minute validity window")
        if discovered > signed_time + MAX_FUTURE_SKEW:
            raise ContextValidationError("snapshot discovery time is later than its attestation")
        encoded = attestation["signature"]
        if not isinstance(encoded, str) or not 80 <= len(encoded) <= 128:
            raise ContextValidationError("attestation signature must be base64url encoded Ed25519 bytes")
        try:
            signature = base64.b64decode(
                encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True
            )
            if len(signature) != 64:
                raise ValueError
            key.verify(
                signature,
                self.signing_payload(
                    tenant_id=tenant_id, source_ref=source_ref, snapshot=snapshot,
                    key_id=key_id, signed_at=signed_at,
                ),
            )
        except (InvalidSignature, ValueError, binascii.Error) as exc:
            raise ContextValidationError("connector provenance signature verification failed") from exc
        return {
            "algorithm": "Ed25519",
            "key_id": key_id,
            "signed_at": signed_at,
            "verified_snapshot_hash": snapshot["snapshot_hash"],
        }

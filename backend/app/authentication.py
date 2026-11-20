"""Verified OIDC bearer authentication for AgentBox.

No caller-controlled headers are used to construct a principal. A Principal is
created only after signature, issuer, audience, time and tenant claims validate.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

import jwt
from fastapi import Header, HTTPException, status
from jwt import PyJWKClient

from .principal import Principal


class AuthenticationError(Exception):
    """A presented credential could not establish a trusted identity."""


class AuthenticationConfigurationError(RuntimeError):
    """OIDC authentication is not configured safely."""


class SigningKeyResolver(Protocol):
    def resolve(self, token: str) -> Any:
        """Return the public signing key for a token's key identifier."""


class OIDCSigningKeyResolver:
    def __init__(self, jwks_uri: str, timeout_seconds: int = 5) -> None:
        self._client = PyJWKClient(jwks_uri, timeout=timeout_seconds, cache_jwk_set=True)

    def resolve(self, token: str) -> Any:
        return self._client.get_signing_key_from_jwt(token).key


@dataclass(frozen=True)
class OIDCAuthenticationConfig:
    issuer: str
    audience: str
    jwks_uri: str
    tenant_claim: str
    algorithms: tuple[str, ...] = ("RS256",)
    leeway_seconds: int = 0
    jwks_timeout_seconds: int = 5

    def __post_init__(self) -> None:
        issuer = urlparse(self.issuer)
        jwks = urlparse(self.jwks_uri)
        allowed = {"RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "EdDSA"}
        if issuer.scheme != "https" or not issuer.netloc:
            raise ValueError("OIDC issuer must be an HTTPS URL")
        if jwks.scheme != "https" or not jwks.netloc:
            raise ValueError("OIDC JWKS URI must be an HTTPS URL")
        if not self.audience or len(self.audience) > 512:
            raise ValueError("OIDC audience must contain 1 to 512 characters")
        if not self.tenant_claim or len(self.tenant_claim) > 128:
            raise ValueError("tenant_claim must contain 1 to 128 characters")
        if not self.algorithms or len(set(self.algorithms)) != len(self.algorithms):
            raise ValueError("At least one unique signing algorithm is required")
        if any(item not in allowed for item in self.algorithms):
            raise ValueError("OIDC signing algorithm is not in the supported asymmetric allowlist")
        if not 0 <= self.leeway_seconds <= 60:
            raise ValueError("leeway_seconds must be between 0 and 60")
        if not 1 <= self.jwks_timeout_seconds <= 15:
            raise ValueError("jwks_timeout_seconds must be between 1 and 15")


def parse_bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise AuthenticationError("Bearer credential is required")
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise AuthenticationError("Authorization header must use the Bearer scheme")
    token = parts[1]
    if len(token) > 16_384 or token.count(".") != 2:
        raise AuthenticationError("Bearer credential has an invalid token envelope")
    return token


class OIDCAuthenticator:
    def __init__(
        self,
        config: OIDCAuthenticationConfig,
        key_resolver: SigningKeyResolver | None = None,
    ) -> None:
        self.config = config
        self.key_resolver = key_resolver or OIDCSigningKeyResolver(
            config.jwks_uri, config.jwks_timeout_seconds
        )

    def authenticate(self, token: str) -> Principal:
        try:
            header = jwt.get_unverified_header(token)
            token_type = header.get("typ")
            if token_type not in ("at+jwt", "application/at+jwt"):
                raise AuthenticationError("Bearer credential is not an RFC 9068 access token")
            signing_key = self.key_resolver.resolve(token)
            claims = jwt.decode(
                token,
                signing_key,
                algorithms=list(self.config.algorithms),
                issuer=self.config.issuer,
                audience=self.config.audience,
                leeway=self.config.leeway_seconds,
                options={"require": ["exp", "iat", "sub", "iss", "aud"]},
            )
        except (jwt.PyJWTError, ValueError, TypeError) as exc:
            raise AuthenticationError("Bearer credential verification failed") from exc

        subject = claims.get("sub")
        tenant_id = claims.get(self.config.tenant_claim)
        if not isinstance(subject, str) or not subject or len(subject) > 256:
            raise AuthenticationError("Verified token has an invalid subject claim")
        if not isinstance(tenant_id, str) or not tenant_id or len(tenant_id) > 256:
            raise AuthenticationError("Verified token is missing a valid tenant claim")
        if "active" in claims and claims["active"] is not True:
            raise AuthenticationError("Verified identity is inactive")
        try:
            return Principal(subject_id=subject, principal_type="user", tenant_id=tenant_id)
        except (TypeError, ValueError) as exc:
            raise AuthenticationError("Verified token could not form an AgentBox principal") from exc


def authenticator_from_environment() -> OIDCAuthenticator:
    issuer = os.getenv("AGENTBOX_OIDC_ISSUER")
    audience = os.getenv("AGENTBOX_OIDC_AUDIENCE")
    jwks_uri = os.getenv("AGENTBOX_OIDC_JWKS_URI")
    tenant_claim = os.getenv("AGENTBOX_OIDC_TENANT_CLAIM")
    if not all((issuer, audience, jwks_uri, tenant_claim)):
        raise AuthenticationConfigurationError("AgentBox OIDC authentication is not configured")
    algorithms = tuple(
        item.strip()
        for item in os.getenv("AGENTBOX_OIDC_ALGORITHMS", "RS256").split(",")
        if item.strip()
    )
    try:
        config = OIDCAuthenticationConfig(
            issuer=issuer or "",
            audience=audience or "",
            jwks_uri=jwks_uri or "",
            tenant_claim=tenant_claim or "",
            algorithms=algorithms,
            leeway_seconds=int(os.getenv("AGENTBOX_OIDC_LEEWAY_SECONDS", "0")),
            jwks_timeout_seconds=int(os.getenv("AGENTBOX_OIDC_JWKS_TIMEOUT_SECONDS", "5")),
        )
    except (TypeError, ValueError) as exc:
        raise AuthenticationConfigurationError("AgentBox OIDC configuration is invalid") from exc
    return OIDCAuthenticator(config)


def require_authenticated_principal(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> Principal:
    """FastAPI dependency; fails closed when auth is unconfigured or credentials fail."""
    try:
        authenticator = authenticator_from_environment()
        token = parse_bearer_token(authorization)
        return authenticator.authenticate(token)
    except AuthenticationConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is not configured",
        ) from exc
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication failed",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

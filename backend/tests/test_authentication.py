from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.authentication import (
    AuthenticationError,
    OIDCAuthenticationConfig,
    OIDCAuthenticator,
    parse_bearer_token,
)
from app.authentication_config import validate_authentication_config


class StaticKeyResolver:
    def __init__(self, key):
        self.key = key

    def resolve(self, token):
        del token
        return self.key


@pytest.fixture
def key_pair():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


@pytest.fixture
def config():
    return OIDCAuthenticationConfig(
        issuer="https://identity.example.test/",
        audience="agentbox-api",
        jwks_uri="https://identity.example.test/.well-known/jwks.json",
        tenant_claim="tenant_id",
    )


def token(private_key, *, header_typ="at+jwt", **overrides):
    now = datetime.now(UTC)
    claims = {
        "iss": "https://identity.example.test/",
        "aud": "agentbox-api",
        "sub": "user-42",
        "tenant_id": "tenant-7",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
    }
    claims.update(overrides)
    return jwt.encode(
        claims, private_key, algorithm="RS256", headers={"typ": header_typ}
    )


def test_bearer_header_is_parsed_strictly():
    assert parse_bearer_token("Bearer abc.def.ghi") == "abc.def.ghi"
    assert parse_bearer_token("bearer abc.def.ghi") == "abc.def.ghi"
    for value in (None, "", "Basic abc.def.ghi", "Bearer", "Bearer a b"):
        with pytest.raises(AuthenticationError):
            parse_bearer_token(value)


def test_verified_token_creates_principal(key_pair, config):
    private_key, public_key = key_pair
    authenticator = OIDCAuthenticator(config, StaticKeyResolver(public_key))
    principal = authenticator.authenticate(token(private_key))
    assert principal.subject_id == "user-42"
    assert principal.tenant_id == "tenant-7"
    assert principal.principal_type == "user"
    assert principal.active is True

@pytest.mark.parametrize("header_typ", ["at+jwt", "application/at+jwt"])
def test_rfc9068_access_token_types_are_accepted(key_pair, config, header_typ):
    private_key, public_key = key_pair
    authenticator = OIDCAuthenticator(config, StaticKeyResolver(public_key))
    principal = authenticator.authenticate(token(private_key, header_typ=header_typ))
    assert principal.subject_id == "user-42"


@pytest.mark.parametrize("header_typ", ["JWT", "application/jwt", ""])
def test_non_access_token_types_are_rejected(key_pair, config, header_typ):
    private_key, public_key = key_pair
    authenticator = OIDCAuthenticator(config, StaticKeyResolver(public_key))
    with pytest.raises(AuthenticationError):
        authenticator.authenticate(token(private_key, header_typ=header_typ))


@pytest.mark.parametrize(
    "claims",
    [
        {"iss": "https://attacker.example/"},
        {"aud": "other-service"},
        {"exp": int((datetime.now(UTC) - timedelta(minutes=1)).timestamp())},
        {"tenant_id": None},
        {"sub": ""},
        {"active": False},
    ],
)
def test_invalid_claims_are_rejected(key_pair, config, claims):
    private_key, public_key = key_pair
    authenticator = OIDCAuthenticator(config, StaticKeyResolver(public_key))
    with pytest.raises(AuthenticationError):
        authenticator.authenticate(token(private_key, **claims))


def test_invalid_signature_is_rejected(key_pair, config):
    private_key, _ = key_pair
    other_private, other_public = rsa.generate_private_key(
        public_exponent=65537, key_size=2048
    ), None
    other_public = other_private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    authenticator = OIDCAuthenticator(config, StaticKeyResolver(other_public))
    with pytest.raises(AuthenticationError):
        authenticator.authenticate(token(private_key))


def test_unsupported_algorithm_is_rejected_by_configuration():
    with pytest.raises(ValueError):
        OIDCAuthenticationConfig(
            issuer="https://identity.example.test/",
            audience="agentbox-api",
            jwks_uri="https://identity.example.test/jwks",
            tenant_claim="tenant_id",
            algorithms=("HS256",),
        )


def test_http_identity_endpoints_are_rejected():
    with pytest.raises(ValueError):
        OIDCAuthenticationConfig(
            issuer="http://identity.example.test/",
            audience="agentbox-api",
            jwks_uri="https://identity.example.test/jwks",
            tenant_claim="tenant_id",
        )


def test_authentication_config_schema_accepts_valid_config():
    value = {
        "schema_version": "1.0",
        "provider": "oidc",
        "issuer": "https://identity.example.test/",
        "audience": "agentbox-api",
        "jwks_uri": "https://identity.example.test/jwks",
        "algorithms": ["RS256"],
        "tenant_claim": "tenant_id",
        "subject_claim": "sub",
        "leeway_seconds": 0,
        "jwks_timeout_seconds": 5,
    }
    assert validate_authentication_config(value) == []


def test_authentication_config_schema_rejects_symmetric_algorithm():
    value = {
        "schema_version": "1.0",
        "provider": "oidc",
        "issuer": "https://identity.example.test/",
        "audience": "agentbox-api",
        "jwks_uri": "https://identity.example.test/jwks",
        "algorithms": ["HS256"],
        "tenant_claim": "tenant_id",
    }
    assert validate_authentication_config(value)
from fastapi import HTTPException

from app.authentication import (
    AuthenticationConfigurationError,
    authenticator_from_environment,
    require_authenticated_principal,
)


def test_authentication_configuration_fails_closed_when_missing(monkeypatch):
    for name in (
        "AGENTBOX_OIDC_ISSUER",
        "AGENTBOX_OIDC_AUDIENCE",
        "AGENTBOX_OIDC_JWKS_URI",
        "AGENTBOX_OIDC_TENANT_CLAIM",
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(AuthenticationConfigurationError):
        authenticator_from_environment()


def test_fastapi_dependency_returns_service_unavailable_when_unconfigured(monkeypatch):
    for name in (
        "AGENTBOX_OIDC_ISSUER",
        "AGENTBOX_OIDC_AUDIENCE",
        "AGENTBOX_OIDC_JWKS_URI",
        "AGENTBOX_OIDC_TENANT_CLAIM",
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(HTTPException) as error:
        require_authenticated_principal(authorization=None)
    assert error.value.status_code == 503

"""Secure source-side PostgreSQL connection construction.

Credentials are resolved just-in-time from an external secret manager. The
AgentBox API and connector registry never receive or persist the DSN.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from .enterprise_context import ContextValidationError

_SECRET_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")


class DatabaseSecretProvider(Protocol):
    """Resolve a secret reference to a PostgreSQL connection string."""

    def resolve(self, secret_ref: str) -> str:
        """Return a DSN/conninfo string; never log or persist the returned value."""
        ...


@dataclass(frozen=True, slots=True)
class PostgreSQLConnectionConfig:
    """Non-secret connection policy for the source-side connector."""

    secret_ref: str
    connect_timeout_seconds: int = 10
    application_name: str = "agentbox-postgresql-catalog"
    sslrootcert: str | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.secret_ref, str)
            or not _SECRET_REF.fullmatch(self.secret_ref)
            or self.secret_ref.lower().startswith(("postgres://", "postgresql://"))
            or any(part in (".", "..") for part in self.secret_ref.split("/"))
        ):
            raise ValueError("secret_ref must be a valid secret-manager reference")
        if (
            type(self.connect_timeout_seconds) is not int
            or not 1 <= self.connect_timeout_seconds <= 30
        ):
            raise ValueError("connect_timeout_seconds must be between 1 and 30")
        if (
            not isinstance(self.application_name, str)
            or not self.application_name
            or len(self.application_name) > 63
            or any(ord(char) < 32 for char in self.application_name)
        ):
            raise ValueError("application_name must be 1-63 printable characters")
        if self.sslrootcert is not None and (
            not isinstance(self.sslrootcert, str)
            or not self.sslrootcert
            or "\x00" in self.sslrootcert
        ):
            raise ValueError("sslrootcert must be a non-empty filesystem path")


class PostgreSQLConnectionFactory:
    """Callable DB-API factory with mandatory certificate and hostname checks.

    The injected connect function is primarily for tests. Production deployments
    should leave it unset so Psycopg 3 is imported lazily.
    """

    def __init__(
        self,
        *,
        config: PostgreSQLConnectionConfig,
        secret_provider: DatabaseSecretProvider,
        connect: Callable[..., Any] | None = None,
    ) -> None:
        if not callable(getattr(secret_provider, "resolve", None)):
            raise TypeError("secret_provider must implement resolve(secret_ref)")
        self.config = config
        self.secret_provider = secret_provider
        self._connect = connect

    def __call__(self):
        try:
            conninfo = self.secret_provider.resolve(self.config.secret_ref)
            if not isinstance(conninfo, str) or not conninfo.strip():
                raise ValueError("empty database secret")
            connect = self._connect
            if connect is None:
                try:
                    import psycopg
                except ImportError as exc:
                    raise RuntimeError("Psycopg 3 is required for PostgreSQL connections") from exc
                connect = psycopg.connect

            options: dict[str, Any] = {
                "sslmode": "verify-full",
                "connect_timeout": self.config.connect_timeout_seconds,
                "application_name": self.config.application_name,
            }
            if self.config.sslrootcert is not None:
                options["sslrootcert"] = self.config.sslrootcert
            return connect(conninfo, **options)
        except Exception:  # noqa: BLE001 - redact sensitive connector exceptions
            # Secret-manager, driver and server exceptions can contain DSNs,
            # usernames, hostnames or passwords. Never propagate their text.
            raise ContextValidationError(
                "Secure PostgreSQL connection could not be established"
            ) from None

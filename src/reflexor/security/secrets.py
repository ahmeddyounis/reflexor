from __future__ import annotations

import os
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SecretRef(BaseModel):
    """Reference to a secret stored outside of Reflexor.

    This model is safe to serialize and include in audit artifacts. The raw secret value must never
    be stored in run packets, logs, or other persisted records—only references like this.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str = Field(min_length=1)
    key: str = Field(min_length=1)
    version: str | None = None

    @field_validator("provider", mode="before")
    @classmethod
    def _normalize_provider(cls, value: object) -> str:
        if not isinstance(value, str):
            raise TypeError("provider must be a string")
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("provider must be non-empty")
        if any(ch.isspace() for ch in normalized):
            raise ValueError("provider must not contain whitespace")
        return normalized

    @field_validator("key", mode="before")
    @classmethod
    def _normalize_key(cls, value: object) -> str:
        if not isinstance(value, str):
            raise TypeError("key must be a string")
        normalized = value.strip()
        if not normalized:
            raise ValueError("key must be non-empty")
        if any(ch.isspace() for ch in normalized):
            raise ValueError("key must not contain whitespace")
        return normalized

    @field_validator("version", mode="before")
    @classmethod
    def _normalize_version(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError("version must be a string")
        normalized = value.strip()
        return normalized or None


class SecretsProvider(Protocol):
    """Resolves a `SecretRef` into a raw secret string.

    The returned value is sensitive and must not be persisted or logged.
    """

    def resolve(self, ref: SecretRef) -> str: ...


def validate_resolved_secret(secret: str) -> str:
    if not isinstance(secret, str):
        raise TypeError("resolved secret must be a string")
    if not secret.strip():
        raise ValueError("resolved secret must be non-empty")
    return secret


class EnvSecretsProvider:
    """Secrets provider backed by environment variables.

    Provider name: `env`.
    Key: environment variable name (exact match).
    """

    provider_name = "env"

    def resolve(self, ref: SecretRef) -> str:
        if ref.provider != self.provider_name:
            raise ValueError(
                f"EnvSecretsProvider cannot resolve provider {ref.provider!r} "
                f"(expected {self.provider_name!r})"
            )

        try:
            return validate_resolved_secret(os.environ[ref.key])
        except KeyError as exc:
            raise KeyError(f"missing environment variable for secret key: {ref.key!r}") from exc

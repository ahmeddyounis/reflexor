from __future__ import annotations

import pytest

from reflexor.security.secrets import EnvSecretsProvider, SecretRef, validate_resolved_secret


def test_validate_resolved_secret_rejects_empty_strings() -> None:
    with pytest.raises(ValueError, match="resolved secret must be non-empty"):
        validate_resolved_secret("")


def test_validate_resolved_secret_rejects_whitespace_only_strings() -> None:
    with pytest.raises(ValueError, match="resolved secret must be non-empty"):
        validate_resolved_secret("   ")


def test_env_secrets_provider_rejects_empty_env_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEBHOOK_SECRET", "")

    provider = EnvSecretsProvider()

    with pytest.raises(ValueError, match="resolved secret must be non-empty"):
        provider.resolve(SecretRef(provider="env", key="WEBHOOK_SECRET"))


def test_env_secrets_provider_rejects_whitespace_only_env_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WEBHOOK_SECRET", "   ")

    provider = EnvSecretsProvider()

    with pytest.raises(ValueError, match="resolved secret must be non-empty"):
        provider.resolve(SecretRef(provider="env", key="WEBHOOK_SECRET"))


def test_env_secrets_provider_returns_existing_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEBHOOK_SECRET", "super-secret")

    provider = EnvSecretsProvider()

    assert provider.resolve(SecretRef(provider="env", key="WEBHOOK_SECRET")) == "super-secret"

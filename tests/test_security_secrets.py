from __future__ import annotations

import pytest

from reflexor.security.secrets import EnvSecretsProvider, SecretRef


def test_secret_ref_round_trip_serialization() -> None:
    ref = SecretRef(provider=" ENV ", key="  API_TOKEN  ", version="  v1  ")
    assert ref.provider == "env"
    assert ref.key == "API_TOKEN"
    assert ref.version == "v1"

    dumped = ref.model_dump()
    restored = SecretRef.model_validate(dumped)
    assert restored == ref


def test_env_secrets_provider_resolves_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_TOKEN", "test-secret-value")
    provider = EnvSecretsProvider()
    assert provider.resolve(SecretRef(provider="env", key="API_TOKEN")) == "test-secret-value"


def test_env_secrets_provider_rejects_wrong_provider() -> None:
    provider = EnvSecretsProvider()
    with pytest.raises(ValueError, match="cannot resolve provider"):
        provider.resolve(SecretRef(provider="vault", key="API_TOKEN"))


def test_env_secrets_provider_errors_on_missing_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISSING_TOKEN", raising=False)
    provider = EnvSecretsProvider()
    with pytest.raises(KeyError, match="missing environment variable"):
        provider.resolve(SecretRef(provider="env", key="MISSING_TOKEN"))

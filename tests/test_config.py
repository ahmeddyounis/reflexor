from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from reflexor.config import ReflexorSettings, clear_settings_cache, get_settings, load_env_file
from reflexor.domain.models_event import DEFAULT_MAX_PAYLOAD_BYTES
from reflexor.domain.models_run_packet import (
    DEFAULT_MAX_PACKET_BYTES,
    DEFAULT_MAX_TOOL_RESULT_BYTES,
)


def test_config_import_and_defaults_are_safe() -> None:
    settings = ReflexorSettings()

    assert settings.profile == "dev"
    assert settings.dry_run is True
    assert settings.allow_side_effects_in_prod is False
    assert settings.enabled_scopes == []
    assert settings.http_allowed_domains == []
    assert settings.webhook_allowed_targets == []
    assert isinstance(settings.workspace_root, Path)
    assert settings.max_event_payload_bytes == DEFAULT_MAX_PAYLOAD_BYTES
    assert settings.max_tool_output_bytes == DEFAULT_MAX_TOOL_RESULT_BYTES
    assert settings.max_run_packet_bytes == DEFAULT_MAX_PACKET_BYTES


def test_settings_load_from_env_and_cache_can_be_cleared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clear_settings_cache()

    monkeypatch.setenv("REFLEXOR_PROFILE", "prod")
    monkeypatch.setenv("REFLEXOR_DRY_RUN", "false")
    monkeypatch.setenv("REFLEXOR_ALLOW_SIDE_EFFECTS_IN_PROD", "true")
    monkeypatch.setenv("REFLEXOR_ENABLED_SCOPES", '["fs.read"]')
    monkeypatch.setenv("REFLEXOR_HTTP_ALLOWED_DOMAINS", '["Example.com", "api.Example.com"]')
    monkeypatch.setenv(
        "REFLEXOR_WEBHOOK_ALLOWED_TARGETS", '["https://hooks.example.com/path", "  "]'
    )
    monkeypatch.setenv("REFLEXOR_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("REFLEXOR_MAX_EVENT_PAYLOAD_BYTES", "123")
    monkeypatch.setenv("REFLEXOR_MAX_TOOL_OUTPUT_BYTES", "456")
    monkeypatch.setenv("REFLEXOR_MAX_RUN_PACKET_BYTES", "789")

    settings_1 = get_settings()
    assert settings_1.profile == "prod"
    assert settings_1.dry_run is False
    assert settings_1.allow_side_effects_in_prod is True
    assert settings_1.enabled_scopes == ["fs.read"]
    assert settings_1.http_allowed_domains == ["example.com", "api.example.com"]
    assert settings_1.webhook_allowed_targets == ["https://hooks.example.com/path"]
    assert settings_1.workspace_root.resolve(strict=False) == tmp_path.resolve(strict=False)
    assert settings_1.max_event_payload_bytes == 123
    assert settings_1.max_tool_output_bytes == 456
    assert settings_1.max_run_packet_bytes == 789

    monkeypatch.setenv("REFLEXOR_PROFILE", "dev")
    settings_2 = get_settings()
    assert settings_2 is settings_1
    assert settings_2.profile == "prod"

    clear_settings_cache()
    settings_3 = get_settings()
    assert settings_3.profile == "dev"
    assert settings_3 is not settings_1


def test_prod_rejects_dry_run_disabled_without_ack(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_settings_cache()

    monkeypatch.setenv("REFLEXOR_PROFILE", "prod")
    monkeypatch.setenv("REFLEXOR_DRY_RUN", "false")
    monkeypatch.delenv("REFLEXOR_ALLOW_SIDE_EFFECTS_IN_PROD", raising=False)

    with pytest.raises(ValueError, match="allow_side_effects_in_prod=True"):
        get_settings()


def test_load_env_file_is_optional(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    has_dotenv = importlib.util.find_spec("dotenv") is not None

    env_path = tmp_path / ".env"
    env_path.write_text(
        'REFLEXOR_PROFILE=prod\nREFLEXOR_ENABLED_SCOPES=["fs.read"]\n',
        encoding="utf-8",
    )

    monkeypatch.delenv("REFLEXOR_PROFILE", raising=False)
    monkeypatch.delenv("REFLEXOR_ENABLED_SCOPES", raising=False)
    clear_settings_cache()

    loaded = load_env_file(env_path)
    if not has_dotenv:
        assert loaded is False
        assert get_settings().profile == "dev"
        return

    assert loaded is True
    clear_settings_cache()
    settings = get_settings()
    assert settings.profile == "prod"
    assert settings.enabled_scopes == ["fs.read"]


def test_unknown_scopes_are_rejected() -> None:
    with pytest.raises(ValueError, match="unknown scope"):
        ReflexorSettings(enabled_scopes=["unknown.scope"])


def test_approval_required_scopes_must_be_enabled() -> None:
    with pytest.raises(ValueError, match="approval_required_scopes must be a subset"):
        ReflexorSettings(enabled_scopes=["fs.read"], approval_required_scopes=["fs.write"])

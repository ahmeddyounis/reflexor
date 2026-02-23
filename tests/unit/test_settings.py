from __future__ import annotations

import os
from pathlib import Path

import pytest

from reflexor.config import ReflexorSettings, clear_settings_cache, get_settings
from reflexor.domain.models_event import DEFAULT_MAX_PAYLOAD_BYTES
from reflexor.domain.models_run_packet import (
    DEFAULT_MAX_PACKET_BYTES,
    DEFAULT_MAX_TOOL_RESULT_BYTES,
)


@pytest.fixture(autouse=True)
def _isolate_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_settings_cache()
    for key in list(os.environ):
        if key.startswith("REFLEXOR_"):
            monkeypatch.delenv(key, raising=False)


def test_defaults_are_safe(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)

    settings = ReflexorSettings()

    assert settings.profile == "dev"
    assert settings.dry_run is True
    assert settings.allow_side_effects_in_prod is False
    assert settings.allow_wildcards is False
    assert settings.enabled_scopes == []
    assert settings.approval_required_scopes == []
    assert settings.http_allowed_domains == []
    assert settings.webhook_allowed_targets == []
    assert settings.workspace_root.resolve(strict=False) == tmp_path.resolve(strict=False)
    assert settings.queue_backend == "inmemory"
    assert settings.queue_visibility_timeout_s == 60.0
    assert settings.max_event_payload_bytes == DEFAULT_MAX_PAYLOAD_BYTES
    assert settings.max_tool_output_bytes == DEFAULT_MAX_TOOL_RESULT_BYTES
    assert settings.max_run_packet_bytes == DEFAULT_MAX_PACKET_BYTES


def test_prod_rejects_dry_run_disabled_without_ack(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    clear_settings_cache()

    monkeypatch.setenv("REFLEXOR_PROFILE", "prod")
    monkeypatch.setenv("REFLEXOR_DRY_RUN", "false")
    monkeypatch.delenv("REFLEXOR_ALLOW_SIDE_EFFECTS_IN_PROD", raising=False)

    with pytest.raises(ValueError, match="allow_side_effects_in_prod=True"):
        get_settings()


def test_dev_allows_dry_run_disabled_without_ack(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)

    settings = ReflexorSettings(profile="dev", dry_run=False)
    assert settings.profile == "dev"
    assert settings.dry_run is False


def test_unknown_scopes_are_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="unknown scope"):
        ReflexorSettings(enabled_scopes=["unknown.scope"])


def test_allowlists_are_normalized(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    clear_settings_cache()

    monkeypatch.setenv(
        "REFLEXOR_HTTP_ALLOWED_DOMAINS", '[" Example.com ", "api.Example.com", "example.com."]'
    )
    monkeypatch.setenv(
        "REFLEXOR_WEBHOOK_ALLOWED_TARGETS", '[" HTTPS://Hooks.Example.com/Path ", "  "]'
    )

    settings = get_settings()
    assert settings.http_allowed_domains == ["example.com", "api.example.com"]
    assert settings.webhook_allowed_targets == ["https://hooks.example.com/Path"]


def test_allowlists_reject_wildcards_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    clear_settings_cache()

    monkeypatch.setenv("REFLEXOR_HTTP_ALLOWED_DOMAINS", '["*.example.com"]')
    with pytest.raises(ValueError, match="wildcards are disabled"):
        get_settings()


def test_workspace_root_relative_paths_are_resolved_and_files_are_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)

    clear_settings_cache()
    monkeypatch.setenv("REFLEXOR_WORKSPACE_ROOT", "workspace")
    settings = get_settings()
    assert settings.workspace_root.resolve(strict=False) == (tmp_path / "workspace").resolve(
        strict=False
    )

    clear_settings_cache()
    not_a_dir = tmp_path / "not-a-dir"
    not_a_dir.write_text("x", encoding="utf-8")
    monkeypatch.setenv("REFLEXOR_WORKSPACE_ROOT", str(not_a_dir))

    with pytest.raises(ValueError, match="must be a directory"):
        get_settings()


def test_queue_backend_is_normalized(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    clear_settings_cache()

    monkeypatch.setenv("REFLEXOR_QUEUE_BACKEND", " INMEMORY ")
    settings = get_settings()
    assert settings.queue_backend == "inmemory"

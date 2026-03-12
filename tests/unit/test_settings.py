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
    assert settings.log_level == "INFO"
    assert settings.enabled_scopes == []
    assert settings.approval_required_scopes == []
    assert settings.http_allowed_domains == []
    assert settings.webhook_allowed_targets == []
    assert settings.workspace_root.resolve(strict=False) == tmp_path.resolve(strict=False)
    assert settings.database_url == "sqlite+aiosqlite:///./reflexor.db"
    assert settings.db_echo is False
    assert settings.db_pool_size is None
    assert settings.db_max_overflow is None
    assert settings.db_pool_timeout_s is None
    assert settings.db_pool_pre_ping is True
    assert settings.queue_backend == "inmemory"
    assert settings.queue_visibility_timeout_s == 60.0
    assert settings.redis_url is None
    assert settings.redis_stream_key == "reflexor:tasks"
    assert settings.redis_consumer_group == "reflexor"
    assert settings.redis_consumer_name.startswith("reflexor-")
    assert settings.redis_stream_maxlen is None
    assert settings.redis_claim_batch_size == 50
    assert settings.redis_promote_batch_size == 50
    assert settings.redis_visibility_timeout_ms == 60_000
    assert settings.redis_delayed_zset_key == "reflexor:tasks:delayed"
    assert settings.executor_max_concurrency == 50
    assert settings.executor_per_tool_concurrency == {}
    assert settings.executor_default_timeout_s == 60.0
    assert settings.executor_visibility_timeout_s == 60.0
    assert settings.executor_retry_base_delay_s == 1.0
    assert settings.executor_retry_max_delay_s == 60.0
    assert settings.executor_retry_jitter == 0.0
    assert settings.rate_limits_enabled is False
    assert settings.rate_limit_default is None
    assert settings.rate_limit_per_tool == {}
    assert settings.rate_limit_per_destination == {}
    assert settings.rate_limit_per_run is None
    assert settings.planner_backend == "noop"
    assert settings.planner_model is None
    assert settings.planner_api_key is None
    assert settings.planner_base_url == "https://api.openai.com/v1"
    assert settings.planner_timeout_s == 30.0
    assert settings.planner_temperature == 0.0
    assert settings.planner_system_prompt is None
    assert settings.planner_max_memory_items == 5
    assert settings.planner_max_tokens_per_run == 4096
    assert settings.approval_required_domains == []
    assert settings.approval_required_payload_keywords == []
    assert settings.otel_enabled is False
    assert settings.otel_service_name == "reflexor"
    assert settings.otel_exporter_otlp_endpoint is None
    assert settings.otel_console_exporter is False
    assert settings.planner_interval_s == 60.0
    assert settings.planner_debounce_s == 2.0
    assert settings.event_backlog_max == 200
    assert settings.max_events_per_planning_cycle == 50
    assert settings.event_dedupe_window_s == 86_400.0
    assert settings.maintenance_batch_size == 200
    assert settings.memory_compaction_after_days == 1
    assert settings.memory_retention_days == 30
    assert settings.archive_terminal_tasks_after_days == 30
    assert settings.max_tasks_per_run == 50
    assert settings.max_tool_calls_per_run == 50
    assert settings.max_run_wall_time_s == 30.0
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

    clear_settings_cache()
    monkeypatch.setenv("REFLEXOR_QUEUE_BACKEND", " REDIS-STREAMS ")
    settings = get_settings()
    assert settings.queue_backend == "redis_streams"


def test_database_settings_are_validated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)

    settings = ReflexorSettings(database_url=" sqlite+aiosqlite:///./reflexor.db ")
    assert settings.database_url == "sqlite+aiosqlite:///./reflexor.db"

    with pytest.raises(ValueError, match="database_url must be non-empty"):
        ReflexorSettings(database_url=" ")

    with pytest.raises(ValueError, match="db_pool_size must be > 0"):
        ReflexorSettings(db_pool_size=0)

    with pytest.raises(ValueError, match="db_max_overflow must be >= 0"):
        ReflexorSettings(db_max_overflow=-1)

    with pytest.raises(ValueError, match="db_pool_timeout_s must be > 0"):
        ReflexorSettings(db_pool_timeout_s=0)


def test_event_suppression_durations_must_be_finite(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValueError, match="event_suppression_window_s must be finite and > 0"):
        ReflexorSettings(event_suppression_window_s=float("nan"))
    with pytest.raises(ValueError, match="event_suppression_ttl_s must be finite and > 0"):
        ReflexorSettings(event_suppression_ttl_s=float("inf"))
    with pytest.raises(ValueError, match="event_dedupe_window_s must be finite and > 0"):
        ReflexorSettings(event_dedupe_window_s=float("inf"))


def test_planner_settings_are_validated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)

    settings = ReflexorSettings(
        planner_backend="openai_compatible",
        planner_model="gpt-test",
        planner_base_url=" https://planner.example.com/v1/ ",
        planner_temperature=0.5,
    )
    assert settings.planner_model == "gpt-test"
    assert settings.planner_base_url == "https://planner.example.com/v1"
    assert settings.planner_temperature == 0.5

    with pytest.raises(ValueError, match="planner_model must be set"):
        ReflexorSettings(planner_backend="openai_compatible")

    with pytest.raises(ValueError, match="planner_base_url must be non-empty"):
        ReflexorSettings(planner_base_url=" ")

    with pytest.raises(ValueError, match="planner_temperature must be in \\[0, 2\\]"):
        ReflexorSettings(planner_temperature=3)

    with pytest.raises(ValueError, match="otel_service_name must be non-empty"):
        ReflexorSettings(otel_service_name=" ")


def test_approval_and_maintenance_settings_are_normalized(tmp_path: Path) -> None:
    settings = ReflexorSettings(
        workspace_root=tmp_path,
        approval_required_domains=[" Example.com ", "api.example.com."],
        approval_required_payload_keywords=[" Secret ", "secret", "PII"],
    )

    assert settings.approval_required_domains == ["example.com", "api.example.com"]
    assert settings.approval_required_payload_keywords == ["secret", "pii"]


def test_redis_settings_reject_invalid_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValueError, match="redis_stream_key must be non-empty"):
        ReflexorSettings(redis_stream_key=" ")

    with pytest.raises(ValueError, match="redis_consumer_group must be non-empty"):
        ReflexorSettings(redis_consumer_group=" ")

    with pytest.raises(ValueError, match="redis_delayed_zset_key must be non-empty"):
        ReflexorSettings(redis_delayed_zset_key=" ")

    with pytest.raises(ValueError, match="redis_stream_maxlen must be > 0"):
        ReflexorSettings(redis_stream_maxlen=0)

    with pytest.raises(ValueError, match="redis_visibility_timeout_ms must be > 0"):
        ReflexorSettings(redis_visibility_timeout_ms=0)

    with pytest.raises(ValueError, match="redis_claim_batch_size must be > 0"):
        ReflexorSettings(redis_claim_batch_size=0)

    with pytest.raises(ValueError, match="redis_promote_batch_size must be > 0"):
        ReflexorSettings(redis_promote_batch_size=0)


def test_prod_requires_redis_url_for_redis_streams_queue(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValueError, match="REFLEXOR_REDIS_URL"):
        ReflexorSettings(profile="prod", queue_backend="redis_streams")

    settings = ReflexorSettings(
        profile="prod", queue_backend="redis_streams", redis_url="redis://x"
    )
    assert settings.redis_url == "redis://x"


def test_orchestrator_settings_reject_non_positive_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValueError, match="planner_interval_s must be > 0"):
        ReflexorSettings(planner_interval_s=0)

    with pytest.raises(ValueError, match="planner_debounce_s must be > 0"):
        ReflexorSettings(planner_debounce_s=-1)

    with pytest.raises(ValueError, match="event_backlog_max must be > 0"):
        ReflexorSettings(event_backlog_max=0)

    with pytest.raises(ValueError, match="max_events_per_planning_cycle must be > 0"):
        ReflexorSettings(max_events_per_planning_cycle=0)

    with pytest.raises(ValueError, match="event_dedupe_window_s must be finite and > 0"):
        ReflexorSettings(event_dedupe_window_s=0)

    with pytest.raises(ValueError, match="maintenance_batch_size must be > 0"):
        ReflexorSettings(maintenance_batch_size=0)

    with pytest.raises(ValueError, match="memory_compaction_after_days must be > 0"):
        ReflexorSettings(memory_compaction_after_days=0)

    with pytest.raises(ValueError, match="memory_retention_days must be > 0"):
        ReflexorSettings(memory_retention_days=0)

    with pytest.raises(ValueError, match="archive_terminal_tasks_after_days must be > 0"):
        ReflexorSettings(archive_terminal_tasks_after_days=0)

    with pytest.raises(ValueError, match="max_tasks_per_run must be > 0"):
        ReflexorSettings(max_tasks_per_run=0)

    with pytest.raises(ValueError, match="max_tool_calls_per_run must be > 0"):
        ReflexorSettings(max_tool_calls_per_run=0)

    with pytest.raises(ValueError, match="planner_max_tokens_per_run must be > 0"):
        ReflexorSettings(planner_max_tokens_per_run=0)

    with pytest.raises(ValueError, match="max_run_wall_time_s must be > 0"):
        ReflexorSettings(max_run_wall_time_s=0)


def test_executor_settings_reject_invalid_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValueError, match="executor_max_concurrency must be > 0"):
        ReflexorSettings(executor_max_concurrency=0)

    with pytest.raises(ValueError, match="executor_default_timeout_s must be > 0"):
        ReflexorSettings(executor_default_timeout_s=0)

    with pytest.raises(ValueError, match="executor_retry_jitter must be in \\[0, 1\\]"):
        ReflexorSettings(executor_retry_jitter=2)

    with pytest.raises(
        ValueError, match="executor_retry_max_delay_s must be >= executor_retry_base_delay_s"
    ):
        ReflexorSettings(executor_retry_base_delay_s=5, executor_retry_max_delay_s=1)

    with pytest.raises(
        ValueError, match="executor_visibility_timeout_s must be >= executor_default_timeout_s"
    ):
        ReflexorSettings(executor_default_timeout_s=20, executor_visibility_timeout_s=10)

    with pytest.raises(
        ValueError, match="executor_per_tool_concurrency values must be <= executor_max_concurrency"
    ):
        ReflexorSettings(executor_max_concurrency=3, executor_per_tool_concurrency={"echo": 4})


def test_executor_per_tool_concurrency_parses_env_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    clear_settings_cache()

    monkeypatch.setenv("REFLEXOR_EXECUTOR_PER_TOOL_CONCURRENCY", '{"echo": 2, "other": 1}')
    settings = get_settings()
    assert settings.executor_per_tool_concurrency == {"echo": 2, "other": 1}

    clear_settings_cache()
    monkeypatch.setenv("REFLEXOR_EXECUTOR_PER_TOOL_CONCURRENCY", " echo=2 , other=1 ")
    settings = get_settings()
    assert settings.executor_per_tool_concurrency == {"echo": 2, "other": 1}


def test_rate_limit_specs_parse_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    clear_settings_cache()

    monkeypatch.setenv("REFLEXOR_RATE_LIMITS_ENABLED", "true")
    monkeypatch.setenv(
        "REFLEXOR_RATE_LIMIT_DEFAULT",
        '{"capacity": 10, "refill_rate_per_s": 5, "burst": 1}',
    )
    monkeypatch.setenv(
        "REFLEXOR_RATE_LIMIT_PER_TOOL",
        '{"NET.HTTP": {"capacity": 2, "refill_rate_per_s": 1}}',
    )
    monkeypatch.setenv(
        "REFLEXOR_RATE_LIMIT_PER_DESTINATION",
        '{"Api.Example.com.": {"capacity": 3, "refill_rate_per_s": 2}}',
    )

    settings = get_settings()
    assert settings.rate_limits_enabled is True
    assert settings.rate_limit_default is not None
    assert settings.rate_limit_default.capacity == 10
    assert settings.rate_limit_default.refill_rate_per_s == 5
    assert settings.rate_limit_default.burst == 1
    assert settings.rate_limit_per_tool["net.http"].capacity == 2
    assert settings.rate_limit_per_destination["api.example.com"].capacity == 3


def test_rate_limit_settings_reject_malformed_specs(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="capacity"):
        ReflexorSettings(
            workspace_root=tmp_path,
            rate_limit_default={"capacity": -1, "refill_rate_per_s": 1},
        )

    with pytest.raises(ValueError, match="refill_rate_per_s"):
        ReflexorSettings(
            workspace_root=tmp_path,
            rate_limit_default={"capacity": 1, "refill_rate_per_s": -1},
        )

    with pytest.raises(ValueError, match="capacity \\+ burst must be > 0"):
        ReflexorSettings(
            workspace_root=tmp_path,
            rate_limit_default={"capacity": 0, "refill_rate_per_s": 1, "burst": 0},
        )

    with pytest.raises(ValueError, match="keys must be non-empty"):
        ReflexorSettings(
            workspace_root=tmp_path,
            rate_limit_per_tool={"   ": {"capacity": 1, "refill_rate_per_s": 1}},
        )

    with pytest.raises(ValueError, match="duplicate tool names"):
        ReflexorSettings(
            workspace_root=tmp_path,
            rate_limit_per_tool={
                "NET.HTTP": {"capacity": 1, "refill_rate_per_s": 1},
                " net.http ": {"capacity": 1, "refill_rate_per_s": 1},
            },
        )

    with pytest.raises(ValueError, match="hostname"):
        ReflexorSettings(
            workspace_root=tmp_path,
            rate_limit_per_destination={
                "https://example.com": {"capacity": 1, "refill_rate_per_s": 1}
            },
        )

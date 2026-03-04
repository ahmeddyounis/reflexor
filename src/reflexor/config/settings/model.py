from __future__ import annotations

import math
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import Field, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from reflexor.config.settings.parsing import (
    RateLimitSpecConfig,
    _parse_rate_limit_spec,
    _parse_rate_limit_spec_dict,
    _parse_str_int_dict,
    _parse_str_list,
)
from reflexor.config.validation import (
    normalize_domains,
    normalize_webhook_targets,
    normalize_workspace_root,
    validate_workspace_root,
)
from reflexor.domain.models_event import DEFAULT_MAX_PAYLOAD_BYTES
from reflexor.domain.models_run_packet import (
    DEFAULT_MAX_PACKET_BYTES,
    DEFAULT_MAX_TOOL_RESULT_BYTES,
)
from reflexor.security.scopes import validate_scopes


def _default_redis_consumer_name() -> str:
    return f"reflexor-{uuid4().hex[:8]}"


class ReflexorSettings(BaseSettings):
    """Runtime configuration for Reflexor.

    Settings are loaded from environment variables by default, using the `REFLEXOR_` prefix.
    Defaults are intentionally conservative (deny-by-default, dry-run enabled).
    """

    model_config = SettingsConfigDict(
        env_prefix="REFLEXOR_",
        extra="ignore",
        enable_decoding=False,
    )

    profile: Literal["dev", "prod"] = "dev"
    dry_run: bool = True
    allow_side_effects_in_prod: bool = False
    allow_wildcards: bool = False

    log_level: str = "INFO"

    enable_tool_entrypoints: bool = False
    allow_unsupported_tools: bool = False
    trusted_tool_packages: list[str] = Field(default_factory=list)
    blocked_tool_packages: list[str] = Field(default_factory=list)

    admin_api_key: str | None = None
    events_require_admin: bool = False
    api_url: str | None = None

    enabled_scopes: list[str] = Field(default_factory=list)
    approval_required_scopes: list[str] = Field(default_factory=list)
    http_allowed_domains: list[str] = Field(default_factory=list)
    webhook_allowed_targets: list[str] = Field(default_factory=list)
    net_safety_resolve_dns: bool = False
    net_safety_dns_timeout_s: float = 0.5
    workspace_root: Path = Field(default_factory=Path.cwd)

    # Tool sandboxing (best-effort subprocess isolation). Disabled by default.
    sandbox_enabled: bool = False
    sandbox_tools: list[str] = Field(default_factory=list)
    sandbox_env_allowlist: list[str] = Field(default_factory=list)
    sandbox_max_memory_mb: int | None = None
    sandbox_python_executable: str | None = None

    reflex_rules_path: Path | None = None

    database_url: str = "sqlite+aiosqlite:///./reflexor.db"
    db_echo: bool = False
    db_pool_size: int | None = None
    db_max_overflow: int | None = None
    db_pool_timeout_s: float | None = None
    db_pool_pre_ping: bool = True

    queue_backend: Literal["inmemory", "redis_streams"] = "inmemory"
    queue_visibility_timeout_s: float = 60.0

    # Redis Streams queue backend settings (queue_backend=redis_streams).
    # Safe to leave unset when using the default in-memory queue.
    redis_url: str | None = None
    redis_stream_key: str = "reflexor:tasks"
    redis_consumer_group: str = "reflexor"
    redis_consumer_name: str = Field(default_factory=_default_redis_consumer_name)
    redis_stream_maxlen: int | None = None
    redis_claim_batch_size: int = 50
    redis_promote_batch_size: int = 50
    redis_visibility_timeout_ms: int = 60_000
    redis_delayed_zset_key: str = "reflexor:tasks:delayed"

    executor_max_concurrency: int = 50
    executor_per_tool_concurrency: dict[str, int] = Field(default_factory=dict)
    executor_default_timeout_s: float = 60.0
    executor_visibility_timeout_s: float = 60.0
    executor_retry_base_delay_s: float = 1.0
    executor_retry_max_delay_s: float = 60.0
    executor_retry_jitter: float = 0.0

    # Rate limiting (execution guards). Disabled unless explicitly enabled.
    rate_limits_enabled: bool = False
    rate_limit_default: RateLimitSpecConfig | None = None
    rate_limit_per_tool: dict[str, RateLimitSpecConfig] = Field(default_factory=dict)
    rate_limit_per_destination: dict[str, RateLimitSpecConfig] = Field(default_factory=dict)
    rate_limit_per_run: RateLimitSpecConfig | None = None

    planner_interval_s: float = 60.0
    planner_debounce_s: float = 2.0
    event_backlog_max: int = 200
    max_events_per_planning_cycle: int = 50

    max_tasks_per_run: int = 50
    max_tool_calls_per_run: int = 50
    max_run_wall_time_s: float = 30.0

    max_event_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES
    max_tool_output_bytes: int = DEFAULT_MAX_TOOL_RESULT_BYTES
    max_run_packet_bytes: int = DEFAULT_MAX_PACKET_BYTES

    # Event suppression (runaway loop protection). Disabled unless explicitly enabled.
    event_suppression_enabled: bool = False
    event_suppression_signature_fields: list[str] = Field(default_factory=list)
    event_suppression_window_s: float = 60.0
    event_suppression_threshold: int = 50
    event_suppression_ttl_s: float = 300.0

    @field_validator("admin_api_key")
    @classmethod
    def _normalize_admin_api_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = str(value).strip()
        if not trimmed:
            return None
        return trimmed

    @field_validator("api_url")
    @classmethod
    def _normalize_api_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = str(value).strip()
        if not trimmed:
            return None
        return trimmed

    @field_validator("net_safety_dns_timeout_s")
    @classmethod
    def _validate_net_safety_dns_timeout_s(cls, value: float) -> float:
        timeout_s = float(value)
        if not math.isfinite(timeout_s):
            raise ValueError("net_safety_dns_timeout_s must be finite")
        if timeout_s <= 0:
            raise ValueError("net_safety_dns_timeout_s must be > 0")
        return timeout_s

    @field_validator("redis_url")
    @classmethod
    def _normalize_redis_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = str(value).strip()
        if not trimmed:
            return None
        return trimmed

    @field_validator("sandbox_python_executable")
    @classmethod
    def _normalize_sandbox_python_executable(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = str(value).strip()
        if not trimmed:
            return None
        return trimmed

    @field_validator("log_level")
    @classmethod
    def _normalize_log_level(cls, value: str) -> str:
        trimmed = str(value).strip().upper()
        if trimmed == "WARN":
            trimmed = "WARNING"
        if trimmed == "FATAL":
            trimmed = "CRITICAL"

        if trimmed not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("log_level must be one of DEBUG, INFO, WARNING, ERROR, CRITICAL")
        return trimmed

    @field_validator("reflex_rules_path", mode="before")
    @classmethod
    def _normalize_reflex_rules_path(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            trimmed = value.strip()
            return None if not trimmed else trimmed
        return value

    @field_validator(
        "enabled_scopes",
        "approval_required_scopes",
        "http_allowed_domains",
        "webhook_allowed_targets",
        "sandbox_tools",
        "sandbox_env_allowlist",
        "trusted_tool_packages",
        "blocked_tool_packages",
        mode="before",
    )
    @classmethod
    def _parse_list_fields(cls, value: object, info: ValidationInfo) -> list[str]:
        field_name = info.field_name
        assert field_name is not None
        return _parse_str_list(value, field_name=field_name)

    @field_validator("event_suppression_signature_fields", mode="before")
    @classmethod
    def _parse_event_suppression_signature_fields(
        cls, value: object, info: ValidationInfo
    ) -> list[str]:
        field_name = info.field_name
        assert field_name is not None
        return _parse_str_list(value, field_name=field_name)

    @field_validator("event_suppression_signature_fields", mode="after")
    @classmethod
    def _validate_event_suppression_signature_fields(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in value:
            trimmed = raw.strip()
            if not trimmed:
                continue
            if trimmed in seen:
                continue
            seen.add(trimmed)
            normalized.append(trimmed)
        return normalized

    @field_validator("event_suppression_window_s", "event_suppression_ttl_s")
    @classmethod
    def _validate_positive_floats(cls, value: float, info: ValidationInfo) -> float:
        field_name = info.field_name or "value"
        parsed = float(value)
        if parsed <= 0:
            raise ValueError(f"{field_name} must be > 0")
        return parsed

    @field_validator("event_suppression_threshold")
    @classmethod
    def _validate_event_suppression_threshold(cls, value: int) -> int:
        parsed = int(value)
        if parsed <= 0:
            raise ValueError("event_suppression_threshold must be > 0")
        return parsed

    @field_validator("sandbox_max_memory_mb")
    @classmethod
    def _validate_sandbox_max_memory_mb(cls, value: int | None) -> int | None:
        if value is None:
            return None
        parsed = int(value)
        if parsed <= 0:
            raise ValueError("sandbox_max_memory_mb must be > 0")
        return parsed

    @field_validator("executor_per_tool_concurrency", mode="before")
    @classmethod
    def _parse_executor_per_tool_concurrency(
        cls, value: object, info: ValidationInfo
    ) -> dict[str, int]:
        field_name = info.field_name
        assert field_name is not None
        return _parse_str_int_dict(value, field_name=field_name)

    @field_validator("rate_limit_default", "rate_limit_per_run", mode="before")
    @classmethod
    def _parse_rate_limit_spec_fields(
        cls, value: object, info: ValidationInfo
    ) -> RateLimitSpecConfig | None:
        field_name = info.field_name
        assert field_name is not None
        return _parse_rate_limit_spec(value, field_name=field_name)

    @field_validator("rate_limit_per_tool", "rate_limit_per_destination", mode="before")
    @classmethod
    def _parse_rate_limit_spec_dict_fields(
        cls, value: object, info: ValidationInfo
    ) -> dict[str, RateLimitSpecConfig]:
        field_name = info.field_name
        assert field_name is not None
        return _parse_rate_limit_spec_dict(value, field_name=field_name)

    @field_validator("enabled_scopes", "approval_required_scopes", mode="after")
    @classmethod
    def _validate_scopes(cls, value: list[str]) -> list[str]:
        return validate_scopes(value)

    @field_validator("http_allowed_domains", mode="after")
    @classmethod
    def _validate_http_allowed_domains(cls, value: list[str], info: ValidationInfo) -> list[str]:
        allow_wildcards = bool(info.data.get("allow_wildcards", False))
        return normalize_domains(value, allow_wildcards=allow_wildcards)

    @field_validator("webhook_allowed_targets", mode="after")
    @classmethod
    def _validate_webhook_allowed_targets(cls, value: list[str], info: ValidationInfo) -> list[str]:
        allow_wildcards = bool(info.data.get("allow_wildcards", False))
        return normalize_webhook_targets(value, allow_wildcards=allow_wildcards)

    @field_validator("workspace_root", mode="after")
    @classmethod
    def _validate_workspace_root(cls, value: Path) -> Path:
        normalized = normalize_workspace_root(value)
        return validate_workspace_root(normalized)

    @field_validator("database_url")
    @classmethod
    def _validate_database_url(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("database_url must be non-empty")
        return trimmed

    @field_validator("db_pool_size")
    @classmethod
    def _validate_db_pool_size(cls, value: int | None) -> int | None:
        if value is None:
            return None
        size = int(value)
        if size <= 0:
            raise ValueError("db_pool_size must be > 0")
        return size

    @field_validator("db_max_overflow")
    @classmethod
    def _validate_db_max_overflow(cls, value: int | None) -> int | None:
        if value is None:
            return None
        overflow = int(value)
        if overflow < 0:
            raise ValueError("db_max_overflow must be >= 0")
        return overflow

    @field_validator("db_pool_timeout_s")
    @classmethod
    def _validate_db_pool_timeout_s(cls, value: float | None) -> float | None:
        if value is None:
            return None
        timeout_s = float(value)
        if timeout_s <= 0:
            raise ValueError("db_pool_timeout_s must be > 0")
        return timeout_s

    @field_validator("queue_backend", mode="before")
    @classmethod
    def _normalize_queue_backend(cls, value: object) -> str:
        if value is None:
            return "inmemory"
        if isinstance(value, str):
            normalized = value.strip().lower().replace("-", "_")
            if not normalized:
                raise ValueError("queue_backend must be non-empty")
            return normalized
        raise TypeError("queue_backend must be a string")

    @field_validator("queue_visibility_timeout_s")
    @classmethod
    def _validate_queue_visibility_timeout_s(cls, value: float) -> float:
        timeout_s = float(value)
        if timeout_s <= 0:
            raise ValueError("queue_visibility_timeout_s must be > 0")
        return timeout_s

    @field_validator(
        "redis_stream_key",
        "redis_consumer_group",
        "redis_consumer_name",
        "redis_delayed_zset_key",
    )
    @classmethod
    def _validate_redis_non_empty_strings(cls, value: str, info: ValidationInfo) -> str:
        field_name = info.field_name or "value"
        trimmed = str(value).strip()
        if not trimmed:
            raise ValueError(f"{field_name} must be non-empty")
        return trimmed

    @field_validator("redis_stream_maxlen")
    @classmethod
    def _validate_redis_stream_maxlen(cls, value: int | None) -> int | None:
        if value is None:
            return None
        maxlen = int(value)
        if maxlen <= 0:
            raise ValueError("redis_stream_maxlen must be > 0")
        return maxlen

    @field_validator("redis_claim_batch_size", "redis_promote_batch_size")
    @classmethod
    def _validate_redis_positive_ints(cls, value: int, info: ValidationInfo) -> int:
        field_name = info.field_name or "value"
        number = int(value)
        if number <= 0:
            raise ValueError(f"{field_name} must be > 0")
        return number

    @field_validator("redis_visibility_timeout_ms")
    @classmethod
    def _validate_redis_visibility_timeout_ms(cls, value: int) -> int:
        timeout_ms = int(value)
        if timeout_ms <= 0:
            raise ValueError("redis_visibility_timeout_ms must be > 0")
        return timeout_ms

    @field_validator(
        "executor_default_timeout_s",
        "executor_visibility_timeout_s",
        "executor_retry_base_delay_s",
        "executor_retry_max_delay_s",
        "planner_interval_s",
        "planner_debounce_s",
        "max_run_wall_time_s",
    )
    @classmethod
    def _validate_positive_seconds(cls, value: float, info: ValidationInfo) -> float:
        field_name = info.field_name or "seconds"
        seconds = float(value)
        if seconds <= 0:
            raise ValueError(f"{field_name} must be > 0")
        return seconds

    @field_validator(
        "executor_max_concurrency",
        "event_backlog_max",
        "max_events_per_planning_cycle",
        "max_tasks_per_run",
        "max_tool_calls_per_run",
    )
    @classmethod
    def _validate_positive_ints(cls, value: int, info: ValidationInfo) -> int:
        field_name = info.field_name or "value"
        number = int(value)
        if number <= 0:
            raise ValueError(f"{field_name} must be > 0")
        return number

    @field_validator("executor_retry_jitter")
    @classmethod
    def _validate_executor_retry_jitter(cls, value: float) -> float:
        jitter = float(value)
        if jitter < 0 or jitter > 1:
            raise ValueError("executor_retry_jitter must be in [0, 1]")
        return jitter

    @field_validator("executor_per_tool_concurrency", mode="after")
    @classmethod
    def _validate_executor_per_tool_concurrency(
        cls, value: dict[str, int], info: ValidationInfo
    ) -> dict[str, int]:
        max_concurrency = int(info.data.get("executor_max_concurrency", 0) or 0)

        normalized: dict[str, int] = {}
        for tool_name, raw_limit in value.items():
            normalized_tool_name = tool_name.strip()
            if not normalized_tool_name:
                raise ValueError("executor_per_tool_concurrency keys must be non-empty")

            limit = int(raw_limit)
            if limit <= 0:
                raise ValueError("executor_per_tool_concurrency values must be > 0")
            if max_concurrency and limit > max_concurrency:
                raise ValueError(
                    "executor_per_tool_concurrency values must be <= executor_max_concurrency"
                )

            if normalized_tool_name in normalized:
                raise ValueError(
                    "executor_per_tool_concurrency contains duplicate tool names "
                    "after normalization"
                )
            normalized[normalized_tool_name] = limit

        return normalized

    @field_validator("rate_limit_per_tool", mode="after")
    @classmethod
    def _validate_rate_limit_per_tool(
        cls, value: dict[str, RateLimitSpecConfig]
    ) -> dict[str, RateLimitSpecConfig]:
        normalized: dict[str, RateLimitSpecConfig] = {}
        for tool_name, spec in value.items():
            normalized_tool_name = tool_name.strip().lower()
            if not normalized_tool_name:
                raise ValueError("rate_limit_per_tool keys must be non-empty")
            if normalized_tool_name in normalized:
                raise ValueError(
                    "rate_limit_per_tool contains duplicate tool names after normalization"
                )
            normalized[normalized_tool_name] = spec
        return normalized

    @field_validator("rate_limit_per_destination", mode="after")
    @classmethod
    def _validate_rate_limit_per_destination(
        cls, value: dict[str, RateLimitSpecConfig]
    ) -> dict[str, RateLimitSpecConfig]:
        normalized: dict[str, RateLimitSpecConfig] = {}
        for hostname, spec in value.items():
            normalized_hostnames = normalize_domains([hostname], allow_wildcards=False)
            normalized_hostname = normalized_hostnames[0]
            if normalized_hostname in normalized:
                raise ValueError(
                    "rate_limit_per_destination contains duplicate hostnames after normalization"
                )
            normalized[normalized_hostname] = spec
        return normalized

    @field_validator("max_event_payload_bytes", "max_tool_output_bytes", "max_run_packet_bytes")
    @classmethod
    def _validate_positive_sizes(cls, value: int, info: ValidationInfo) -> int:
        field_name = info.field_name or "size"
        if value <= 0:
            raise ValueError(f"{field_name} must be > 0")
        return value

    @model_validator(mode="after")
    def _validate_profile_guardrails(self) -> ReflexorSettings:
        if self.profile == "prod" and not self.dry_run and not self.allow_side_effects_in_prod:
            raise ValueError(
                "prod with dry_run=False requires allow_side_effects_in_prod=True "
                "(set REFLEXOR_ALLOW_SIDE_EFFECTS_IN_PROD=true)"
            )
        if self.profile == "prod" and self.allow_unsupported_tools:
            raise ValueError("prod does not allow allow_unsupported_tools=true")
        if self.queue_backend == "redis_streams" and self.profile == "prod" and not self.redis_url:
            raise ValueError(
                "prod with queue_backend=redis_streams requires redis_url "
                "(set REFLEXOR_REDIS_URL=redis://...)"
            )
        if self.executor_retry_max_delay_s < self.executor_retry_base_delay_s:
            raise ValueError("executor_retry_max_delay_s must be >= executor_retry_base_delay_s")
        if self.executor_visibility_timeout_s < self.executor_default_timeout_s:
            raise ValueError("executor_visibility_timeout_s must be >= executor_default_timeout_s")
        unknown_approval_scopes = sorted(
            set(self.approval_required_scopes) - set(self.enabled_scopes)
        )
        if unknown_approval_scopes:
            raise ValueError(
                "approval_required_scopes must be a subset of enabled_scopes "
                f"(not enabled: {unknown_approval_scopes})"
            )
        return self

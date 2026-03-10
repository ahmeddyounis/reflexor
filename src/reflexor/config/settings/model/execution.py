from __future__ import annotations

from typing import Literal

from pydantic import Field, ValidationInfo, field_validator

from reflexor.config.settings.model.infra import _ReflexorSettingsInfra
from reflexor.config.settings.parsing import (
    RateLimitSpecConfig,
    _parse_rate_limit_spec,
    _parse_rate_limit_spec_dict,
    _parse_str_int_dict,
    _parse_str_list,
)
from reflexor.config.validation import normalize_domains
from reflexor.domain.models_event import DEFAULT_MAX_PAYLOAD_BYTES
from reflexor.domain.models_run_packet import (
    DEFAULT_MAX_PACKET_BYTES,
    DEFAULT_MAX_TOOL_RESULT_BYTES,
)


class _ReflexorSettingsExecution(_ReflexorSettingsInfra):
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

    planner_backend: Literal["noop", "heuristic", "openai_compatible"] = "noop"
    planner_model: str | None = None
    planner_api_key: str | None = None
    planner_base_url: str = "https://api.openai.com/v1"
    planner_timeout_s: float = 30.0
    planner_temperature: float = 0.0
    planner_system_prompt: str | None = None
    planner_max_memory_items: int = 5
    planner_max_tokens_per_run: int = 4096
    approval_required_domains: list[str] = Field(default_factory=list)
    approval_required_payload_keywords: list[str] = Field(default_factory=list)
    otel_enabled: bool = False
    otel_service_name: str = "reflexor"
    otel_exporter_otlp_endpoint: str | None = None
    otel_console_exporter: bool = False
    planner_interval_s: float = 60.0
    planner_debounce_s: float = 2.0
    event_backlog_max: int = 200
    max_events_per_planning_cycle: int = 50
    maintenance_batch_size: int = 200
    memory_compaction_after_days: int = 1
    memory_retention_days: int | None = 30
    archive_terminal_tasks_after_days: int | None = 30

    max_tasks_per_run: int = 50
    max_tool_calls_per_run: int = 50
    max_run_wall_time_s: float = 30.0

    max_event_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES
    max_tool_output_bytes: int = DEFAULT_MAX_TOOL_RESULT_BYTES
    max_run_packet_bytes: int = DEFAULT_MAX_PACKET_BYTES

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

    @field_validator(
        "executor_default_timeout_s",
        "executor_visibility_timeout_s",
        "executor_retry_base_delay_s",
        "executor_retry_max_delay_s",
        "planner_timeout_s",
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
        "maintenance_batch_size",
        "memory_compaction_after_days",
        "max_tasks_per_run",
        "max_tool_calls_per_run",
        "planner_max_memory_items",
        "planner_max_tokens_per_run",
    )
    @classmethod
    def _validate_positive_ints(cls, value: int, info: ValidationInfo) -> int:
        field_name = info.field_name or "value"
        number = int(value)
        if number <= 0:
            raise ValueError(f"{field_name} must be > 0")
        return number

    @field_validator("memory_retention_days", "archive_terminal_tasks_after_days")
    @classmethod
    def _validate_optional_positive_ints(
        cls, value: int | None, info: ValidationInfo
    ) -> int | None:
        if value is None:
            return None
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

    @field_validator(
        "planner_model",
        "planner_api_key",
        "planner_system_prompt",
        "otel_exporter_otlp_endpoint",
    )
    @classmethod
    def _normalize_optional_planner_strings(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = str(value).strip()
        return trimmed or None

    @field_validator("planner_base_url", "otel_service_name")
    @classmethod
    def _validate_non_empty_urlish_strings(cls, value: str, info: ValidationInfo) -> str:
        trimmed = value.strip()
        if not trimmed:
            field_name = info.field_name or "value"
            raise ValueError(f"{field_name} must be non-empty")
        if info.field_name == "planner_base_url":
            return trimmed.rstrip("/")
        return trimmed

    @field_validator(
        "approval_required_domains",
        "approval_required_payload_keywords",
        mode="before",
    )
    @classmethod
    def _parse_string_lists(cls, value: object, info: ValidationInfo) -> list[str]:
        field_name = info.field_name
        assert field_name is not None
        return _parse_str_list(value, field_name=field_name)

    @field_validator("planner_temperature")
    @classmethod
    def _validate_planner_temperature(cls, value: float) -> float:
        temperature = float(value)
        if temperature < 0 or temperature > 2:
            raise ValueError("planner_temperature must be in [0, 2]")
        return temperature

    @field_validator("approval_required_domains", mode="after")
    @classmethod
    def _validate_approval_required_domains(cls, value: list[str]) -> list[str]:
        return normalize_domains(value)

    @field_validator("approval_required_payload_keywords", mode="after")
    @classmethod
    def _normalize_payload_keywords(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for keyword in value:
            lowered = keyword.strip().lower()
            if not lowered or lowered in seen:
                continue
            seen.add(lowered)
            normalized.append(lowered)
        return normalized

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

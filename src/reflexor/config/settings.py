from __future__ import annotations

import importlib
import importlib.util
import json
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Literal, cast

from pydantic import Field, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

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


def _dedupe_preserving_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _parse_str_list(value: object, *, field_name: str) -> list[str]:
    if value is None:
        return []

    items: list[str]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []

        if text.startswith("["):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = None
            else:
                if not isinstance(parsed, list):
                    raise TypeError(f"{field_name} must be a JSON array or comma-separated string")
                if not all(isinstance(item, str) for item in parsed):
                    raise TypeError(f"{field_name} entries must be strings")
                items = [item.strip() for item in parsed]
                items = [item for item in items if item]
                return _dedupe_preserving_order(items)

        items = [part.strip() for part in text.split(",")]
        items = [item for item in items if item]
        return _dedupe_preserving_order(items)

    if isinstance(value, list):
        if not all(isinstance(item, str) for item in value):
            raise TypeError(f"{field_name} entries must be strings")
        items = [item.strip() for item in value]
        items = [item for item in items if item]
        return _dedupe_preserving_order(items)

    raise TypeError(f"{field_name} must be a list[str] or str")


def _parse_str_int_dict(value: object, *, field_name: str) -> dict[str, int]:
    if value is None:
        return {}

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}

        if text.startswith("{"):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = None
            if not isinstance(parsed, dict):
                raise TypeError(f"{field_name} must be a JSON object or comma-separated pairs")
            parsed_json_dict: dict[str, int] = {}
            for key, parsed_value in parsed.items():
                if not isinstance(key, str):
                    raise TypeError(f"{field_name} keys must be strings")
                if isinstance(parsed_value, bool):
                    raise TypeError(f"{field_name} values must be integers")
                if isinstance(parsed_value, int):
                    parsed_json_dict[key] = parsed_value
                    continue
                if isinstance(parsed_value, str):
                    parsed_json_dict[key] = int(parsed_value.strip())
                    continue
                raise TypeError(f"{field_name} values must be integers")
            return parsed_json_dict

        parsed_pairs: dict[str, int] = {}
        for part in text.split(","):
            item = part.strip()
            if not item:
                continue
            if "=" not in item:
                raise TypeError(
                    f"{field_name} must be a JSON object or comma-separated pairs like tool=3"
                )
            tool_name, raw_limit = item.split("=", 1)
            parsed_pairs[tool_name.strip()] = int(raw_limit.strip())
        return parsed_pairs

    if isinstance(value, dict):
        parsed_dict: dict[str, int] = {}
        for key, parsed_value in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{field_name} keys must be strings")
            if isinstance(parsed_value, bool):
                raise TypeError(f"{field_name} values must be integers")
            if isinstance(parsed_value, int):
                parsed_dict[key] = parsed_value
                continue
            if isinstance(parsed_value, str):
                parsed_dict[key] = int(parsed_value.strip())
                continue
            raise TypeError(f"{field_name} values must be integers")
        return parsed_dict

    raise TypeError(f"{field_name} must be a dict[str,int] or str")


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

    admin_api_key: str | None = None
    events_require_admin: bool = False
    api_url: str | None = None

    enabled_scopes: list[str] = Field(default_factory=list)
    approval_required_scopes: list[str] = Field(default_factory=list)
    http_allowed_domains: list[str] = Field(default_factory=list)
    webhook_allowed_targets: list[str] = Field(default_factory=list)
    workspace_root: Path = Field(default_factory=Path.cwd)

    database_url: str = "sqlite+aiosqlite:///./reflexor.db"
    db_echo: bool = False
    db_pool_size: int | None = None
    db_pool_timeout_s: float | None = None

    queue_backend: Literal["inmemory"] = "inmemory"
    queue_visibility_timeout_s: float = 60.0

    executor_max_concurrency: int = 50
    executor_per_tool_concurrency: dict[str, int] = Field(default_factory=dict)
    executor_default_timeout_s: float = 60.0
    executor_visibility_timeout_s: float = 60.0
    executor_retry_base_delay_s: float = 1.0
    executor_retry_max_delay_s: float = 60.0
    executor_retry_jitter: float = 0.0

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

    @field_validator(
        "enabled_scopes",
        "approval_required_scopes",
        "http_allowed_domains",
        "webhook_allowed_targets",
        mode="before",
    )
    @classmethod
    def _parse_list_fields(cls, value: object, info: ValidationInfo) -> list[str]:
        field_name = info.field_name
        assert field_name is not None
        return _parse_str_list(value, field_name=field_name)

    @field_validator("executor_per_tool_concurrency", mode="before")
    @classmethod
    def _parse_executor_per_tool_concurrency(
        cls, value: object, info: ValidationInfo
    ) -> dict[str, int]:
        field_name = info.field_name
        assert field_name is not None
        return _parse_str_int_dict(value, field_name=field_name)

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
            normalized = value.strip().lower()
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


@lru_cache
def get_settings() -> ReflexorSettings:
    return ReflexorSettings()


def clear_settings_cache() -> None:
    get_settings.cache_clear()


def load_env_file(path: str | Path = ".env", *, override: bool = False) -> bool:
    """Load a dotenv file if `python-dotenv` is installed.

    Returns `True` if the dotenv loader ran successfully, otherwise `False`.
    """

    if importlib.util.find_spec("dotenv") is None:
        return False

    module = importlib.import_module("dotenv")
    loader = getattr(module, "load_dotenv", None)
    if loader is None:
        return False

    load_dotenv = cast(Callable[..., object], loader)
    dotenv_path = str(Path(path))
    return bool(load_dotenv(dotenv_path=dotenv_path, override=override))

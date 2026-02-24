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

    enabled_scopes: list[str] = Field(default_factory=list)
    approval_required_scopes: list[str] = Field(default_factory=list)
    http_allowed_domains: list[str] = Field(default_factory=list)
    webhook_allowed_targets: list[str] = Field(default_factory=list)
    workspace_root: Path = Field(default_factory=Path.cwd)

    queue_backend: Literal["inmemory"] = "inmemory"
    queue_visibility_timeout_s: float = 60.0

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

    @field_validator("planner_interval_s", "planner_debounce_s", "max_run_wall_time_s")
    @classmethod
    def _validate_positive_seconds(cls, value: float, info: ValidationInfo) -> float:
        field_name = info.field_name or "seconds"
        seconds = float(value)
        if seconds <= 0:
            raise ValueError(f"{field_name} must be > 0")
        return seconds

    @field_validator(
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

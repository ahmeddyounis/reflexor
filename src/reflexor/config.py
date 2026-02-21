from __future__ import annotations

import importlib
import importlib.util
import json
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Literal, cast

from pydantic import Field, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from reflexor.domain.models_event import DEFAULT_MAX_PAYLOAD_BYTES
from reflexor.domain.models_run_packet import (
    DEFAULT_MAX_PACKET_BYTES,
    DEFAULT_MAX_TOOL_RESULT_BYTES,
)


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


def _dedupe_preserving_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


class ReflexorSettings(BaseSettings):
    """Runtime configuration for Reflexor.

    Settings are loaded from environment variables by default, using the `REFLEXOR_` prefix.
    Defaults are intentionally conservative (deny-by-default, dry-run enabled).
    """

    model_config = SettingsConfigDict(env_prefix="REFLEXOR_", extra="ignore")

    profile: Literal["dev", "prod"] = "dev"
    dry_run: bool = True
    enabled_scopes: list[str] = Field(default_factory=list)
    http_allowed_domains: list[str] = Field(default_factory=list)
    webhook_allowed_targets: list[str] = Field(default_factory=list)
    workspace_root: Path = Field(default_factory=Path.cwd)

    max_event_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES
    max_tool_output_bytes: int = DEFAULT_MAX_TOOL_RESULT_BYTES
    max_run_packet_bytes: int = DEFAULT_MAX_PACKET_BYTES

    @field_validator(
        "enabled_scopes",
        "http_allowed_domains",
        "webhook_allowed_targets",
        mode="before",
    )
    @classmethod
    def _validate_str_lists(cls, value: object, info: ValidationInfo) -> list[str]:
        field_name = info.field_name
        assert field_name is not None
        parsed = _parse_str_list(value, field_name=field_name)

        if field_name == "http_allowed_domains":
            return [item.lower() for item in parsed]
        return parsed

    @field_validator("workspace_root")
    @classmethod
    def _normalize_workspace_root(cls, value: Path) -> Path:
        return value.expanduser()

    @field_validator("max_event_payload_bytes", "max_tool_output_bytes", "max_run_packet_bytes")
    @classmethod
    def _validate_positive_sizes(cls, value: int, info: ValidationInfo) -> int:
        field_name = info.field_name or "size"
        if value <= 0:
            raise ValueError(f"{field_name} must be > 0")
        return value


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

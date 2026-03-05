from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class _ReflexorSettingsBase(BaseSettings):
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

    admin_api_key: str | None = None
    events_require_admin: bool = False
    api_url: str | None = None

    reflex_rules_path: Path | None = None

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

    @field_validator("reflex_rules_path", mode="before")
    @classmethod
    def _normalize_reflex_rules_path(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            trimmed = value.strip()
            return None if not trimmed else trimmed
        return value

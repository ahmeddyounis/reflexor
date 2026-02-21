from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator


def _require_non_empty_str(value: str, *, field_name: str) -> str:
    trimmed = value.strip()
    if not trimmed:
        raise ValueError(f"{field_name} must be non-empty")
    return trimmed


def _require_json_serializable(value: object, *, field_name: str) -> None:
    try:
        json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except TypeError as exc:
        raise ValueError(f"{field_name} must be JSON-serializable") from exc


def _dedupe_preserving_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


class ToolManifest(BaseModel):
    """Stable metadata describing a tool.

    This schema is intended to be consumed by policy checks, executors, and audit pipelines.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    version: str
    description: str
    permission_scope: str
    side_effects: bool = False
    idempotent: bool = False
    default_timeout_s: int = 60
    max_output_bytes: int = 64_000
    tags: list[str] = Field(default_factory=list)

    @field_validator("name", "version", "description", "permission_scope")
    @classmethod
    def _validate_required_strs(cls, value: str, info: ValidationInfo) -> str:
        field_name = info.field_name or "value"
        return _require_non_empty_str(value, field_name=field_name)

    @field_validator("default_timeout_s", "max_output_bytes")
    @classmethod
    def _validate_positive_ints(cls, value: int, info: ValidationInfo) -> int:
        field_name = info.field_name or "value"
        if value <= 0:
            raise ValueError(f"{field_name} must be > 0")
        return value

    @field_validator("tags")
    @classmethod
    def _normalize_tags(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for item in value:
            trimmed = item.strip()
            if not trimmed:
                raise ValueError("tags entries must be non-empty")
            normalized.append(trimmed)
        return _dedupe_preserving_order(normalized)


class ToolResult(BaseModel):
    """Stable result envelope for tool execution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ok: bool
    data: object | None = None
    error_code: str | None = None
    error_message: str | None = None
    debug: dict[str, object] | None = None
    produced_artifacts: list[dict[str, object]] | None = None

    @field_validator("error_code", "error_message")
    @classmethod
    def _normalize_optional_strs(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return None
        field_name = info.field_name or "value"
        trimmed = value.strip()
        if not trimmed:
            raise ValueError(f"{field_name} must be non-empty when provided")
        return trimmed

    @field_validator("data", "debug", "produced_artifacts")
    @classmethod
    def _validate_json_safe_fields(cls, value: object, info: ValidationInfo) -> object:
        field_name = info.field_name or "value"
        if value is None:
            return None
        _require_json_serializable(value, field_name=field_name)
        return value

    @model_validator(mode="after")
    def _validate_invariants(self) -> ToolResult:
        if self.ok:
            if self.error_code is not None or self.error_message is not None:
                raise ValueError("ok=true must not include error_code/error_message")
            return self

        if self.error_message is None:
            raise ValueError("ok=false requires error_message")
        return self

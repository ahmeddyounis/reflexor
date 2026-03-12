from __future__ import annotations

import math
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from reflexor.config import ReflexorSettings
from reflexor.tools.sdk import ToolResult

_SANDBOX_RESPONSE_MARKER = b"REFLEXOR_SANDBOX_RESPONSE_V1\n"


def _normalize_non_empty_str(value: str, *, field_name: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{field_name} must be non-empty")
    return text


def _extract_sandbox_json(stdout_bytes: bytes) -> bytes | None:
    idx = stdout_bytes.rfind(_SANDBOX_RESPONSE_MARKER)
    if idx < 0:
        return None
    start = idx + len(_SANDBOX_RESPONSE_MARKER)
    return stdout_bytes[start:].strip()


def _sandbox_settings_payload(settings: ReflexorSettings) -> dict[str, object]:
    # Keep this payload minimal: avoid leaking DB/redis credentials to the sandbox by default.
    return {
        "profile": settings.profile,
        "dry_run": bool(settings.dry_run),
        "allow_side_effects_in_prod": bool(settings.allow_side_effects_in_prod),
        "allow_wildcards": bool(settings.allow_wildcards),
        "enable_tool_entrypoints": bool(settings.enable_tool_entrypoints),
        "allow_unsupported_tools": bool(settings.allow_unsupported_tools),
        "trusted_tool_packages": list(settings.trusted_tool_packages),
        "blocked_tool_packages": list(settings.blocked_tool_packages),
        "enabled_scopes": list(settings.enabled_scopes),
        "approval_required_scopes": list(settings.approval_required_scopes),
        "http_allowed_domains": list(settings.http_allowed_domains),
        "webhook_allowed_targets": list(settings.webhook_allowed_targets),
        "workspace_root": str(settings.workspace_root),
        "max_event_payload_bytes": int(settings.max_event_payload_bytes),
        "max_tool_output_bytes": int(settings.max_tool_output_bytes),
        "net_safety_resolve_dns": bool(settings.net_safety_resolve_dns),
        "net_safety_dns_timeout_s": float(settings.net_safety_dns_timeout_s),
    }


class _SandboxToolContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    workspace_root: str
    dry_run: bool = True
    timeout_s: float
    correlation_ids: dict[str, str | None] = Field(default_factory=dict)

    @field_validator("workspace_root")
    @classmethod
    def _validate_workspace_root(cls, value: str) -> str:
        text = _normalize_non_empty_str(value, field_name="workspace_root")
        path = Path(text).expanduser()
        if not path.is_absolute():
            raise ValueError("workspace_root must be an absolute path")
        return str(path)

    @field_validator("timeout_s")
    @classmethod
    def _validate_timeout_s(cls, value: float) -> float:
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("timeout_s must be finite")
        if number <= 0:
            raise ValueError("timeout_s must be > 0")
        return number


class _SandboxRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: int = 1
    tool_name: str
    args: dict[str, object]
    ctx: _SandboxToolContext
    settings: dict[str, object]
    registry_factory: str | None = None
    max_memory_mb: int | None = None

    @field_validator("tool_name")
    @classmethod
    def _validate_tool_name(cls, value: str) -> str:
        return _normalize_non_empty_str(value, field_name="tool_name")

    @field_validator("registry_factory")
    @classmethod
    def _validate_registry_factory(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _normalize_non_empty_str(value, field_name="registry_factory")

    @field_validator("max_memory_mb")
    @classmethod
    def _validate_max_memory_mb(cls, value: int | None) -> int | None:
        if value is None:
            return None
        parsed = int(value)
        if parsed <= 0:
            raise ValueError("max_memory_mb must be > 0")
        return parsed


class _SandboxResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: int = 1
    tool_result: ToolResult

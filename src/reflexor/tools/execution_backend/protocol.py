from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from reflexor.config import ReflexorSettings
from reflexor.tools.sdk import ToolResult

_SANDBOX_RESPONSE_MARKER = b"REFLEXOR_SANDBOX_RESPONSE_V1\n"


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


class _SandboxRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: int = 1
    tool_name: str
    args: dict[str, object]
    ctx: _SandboxToolContext
    settings: dict[str, object]
    registry_factory: str | None = None
    max_memory_mb: int | None = None


class _SandboxResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: int = 1
    tool_result: ToolResult

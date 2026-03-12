"""Approval integration points (policy layer).

Clean Architecture:
This module may depend on `reflexor.domain` models (Approval, ToolCall, Task, etc.) and on
configuration/security utilities, but it must not import infrastructure/framework layers.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from typing import Protocol
from urllib.parse import urlsplit

from pydantic import BaseModel

from reflexor.config import ReflexorSettings, get_settings
from reflexor.domain.enums import ApprovalStatus
from reflexor.domain.models import Approval, ToolCall
from reflexor.domain.serialization import canonical_json, stable_sha256
from reflexor.observability.redaction import Redactor
from reflexor.observability.truncation import truncate_str
from reflexor.security.policy.context import ToolSpec
from reflexor.security.policy.decision import PolicyDecision
from reflexor.tools.sdk import ToolManifest


class ApprovalStore(Protocol):
    """Storage interface for approvals (no DB dependency required)."""

    async def create_pending(self, approval: Approval) -> Approval:
        """Create a pending approval request, idempotent by tool_call_id."""
        ...

    async def get(self, approval_id: str) -> Approval | None:
        """Get an approval by id, or None if missing."""
        ...

    async def get_by_tool_call(self, tool_call_id: str) -> Approval | None:
        """Get the approval for a tool_call_id, or None if missing."""
        ...

    async def list_pending(self, limit: int, offset: int) -> list[Approval]:
        """List pending approvals with simple pagination."""
        ...

    async def decide(
        self,
        approval_id: str,
        decision: ApprovalStatus,
        decided_by: str | None,
    ) -> Approval:
        """Approve or deny a pending approval."""
        ...


class InMemoryApprovalStore:
    """In-memory ApprovalStore implementation (intended for tests/local dev)."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._approvals: dict[str, Approval] = {}
        self._by_tool_call: dict[str, str] = {}

    async def create_pending(self, approval: Approval) -> Approval:
        if approval.status != ApprovalStatus.PENDING:
            raise ValueError("create_pending requires approval.status=pending")

        async with self._lock:
            existing_id = self._by_tool_call.get(approval.tool_call_id)
            if existing_id is not None:
                existing = self._approvals[existing_id]
                return existing.model_copy(deep=True)

            stored = approval.model_copy(deep=True)
            self._approvals[stored.approval_id] = stored
            self._by_tool_call[stored.tool_call_id] = stored.approval_id
            return stored.model_copy(deep=True)

    async def get(self, approval_id: str) -> Approval | None:
        async with self._lock:
            approval = self._approvals.get(approval_id)
            return approval.model_copy(deep=True) if approval is not None else None

    async def get_by_tool_call(self, tool_call_id: str) -> Approval | None:
        async with self._lock:
            approval_id = self._by_tool_call.get(tool_call_id)
            if approval_id is None:
                return None
            return self._approvals[approval_id].model_copy(deep=True)

    async def list_pending(self, limit: int, offset: int) -> list[Approval]:
        if limit < 0:
            raise ValueError("limit must be >= 0")
        if offset < 0:
            raise ValueError("offset must be >= 0")
        if limit == 0:
            return []

        async with self._lock:
            pending: list[Approval] = [
                approval
                for approval in self._approvals.values()
                if approval.status == ApprovalStatus.PENDING
            ]
            pending.sort(key=lambda item: (item.created_at_ms, item.approval_id))
            window: Sequence[Approval] = pending[offset : offset + limit]
            return [approval.model_copy(deep=True) for approval in window]

    async def decide(
        self,
        approval_id: str,
        decision: ApprovalStatus,
        decided_by: str | None,
    ) -> Approval:
        if decision not in {ApprovalStatus.APPROVED, ApprovalStatus.DENIED}:
            raise ValueError("decision must be approved or denied")

        async with self._lock:
            approval = self._approvals.get(approval_id)
            if approval is None:
                raise KeyError(f"unknown approval_id: {approval_id}")

            if approval.status == decision:
                return approval.model_copy(deep=True)
            if approval.status != ApprovalStatus.PENDING:
                raise ValueError(f"approval has already been decided as {approval.status.value}")

            updated = (
                approval.approve(decided_by=decided_by)
                if decision == ApprovalStatus.APPROVED
                else approval.deny(decided_by=decided_by)
            )
            self._approvals[approval_id] = updated
            return updated.model_copy(deep=True)


BODY_ARG_KEYS: frozenset[str] = frozenset({"body", "content", "data", "json", "payload", "text"})


class ApprovalBuilder:
    """Build pending approvals with safe previews and stable payload hashes."""

    def __init__(
        self,
        *,
        settings: ReflexorSettings | None = None,
        redactor: Redactor | None = None,
        max_preview_bytes: int = 1_000,
    ) -> None:
        self._settings = settings or get_settings()
        self._redactor = redactor or Redactor()
        self._max_preview_bytes = max_preview_bytes
        if self._max_preview_bytes <= 0:
            raise ValueError("max_preview_bytes must be > 0")

    @property
    def settings(self) -> ReflexorSettings:
        return self._settings

    @property
    def redactor(self) -> Redactor:
        return self._redactor

    @property
    def max_preview_bytes(self) -> int:
        return self._max_preview_bytes

    def build_payload_hash_for_args(self, *, args: dict[str, object]) -> tuple[str, str]:
        """Return (payload_hash, canonical_json_input) for a tool-call args dict."""

        max_bytes = min(
            self.settings.max_event_payload_bytes,
            self.settings.max_tool_output_bytes,
            self.settings.max_run_packet_bytes,
        )
        redacted = self.redactor.redact(args, max_bytes=max_bytes)
        hash_input = canonical_json(redacted)
        return stable_sha256(hash_input), hash_input

    def build_pending(
        self,
        *,
        run_id: str,
        task_id: str,
        tool_call: ToolCall,
        tool_spec: ToolSpec,
        parsed_args: BaseModel,
        decision: PolicyDecision,
    ) -> Approval:
        payload_hash, _hash_input = self.build_payload_hash_for_args(args=tool_call.args)
        preview = self.build_preview(
            tool_call=tool_call,
            tool_spec=tool_spec,
            parsed_args=parsed_args,
            decision=decision,
        )
        return Approval(
            run_id=run_id,
            task_id=task_id,
            tool_call_id=tool_call.tool_call_id,
            created_at_ms=int(time.time() * 1000),
            payload_hash=payload_hash,
            preview=preview,
        )

    def build_preview(
        self,
        *,
        tool_call: ToolCall,
        tool_spec: ToolSpec,
        parsed_args: BaseModel,
        decision: PolicyDecision,
    ) -> str:
        """Return a human-readable, sanitized preview string for an approval request."""

        manifest = tool_spec.manifest
        url = _extract_url(parsed_args)
        url_preview = _safe_url_preview(url) if url is not None else None

        args_dict: dict[str, object] = parsed_args.model_dump(mode="json")
        body_summaries: dict[str, dict[str, object]] = {}
        for key, value in args_dict.items():
            if key.strip().lower() in BODY_ARG_KEYS:
                body_summaries[key] = _summarize_body(value)

        header_keys: list[str] | None = None
        headers_obj = args_dict.get("headers")
        if isinstance(headers_obj, dict):
            header_keys = sorted(str(k).strip() for k in headers_obj if str(k).strip())

        preview_obj: dict[str, object] = {
            "action": decision.action.value,
            "reason_code": decision.reason_code,
            "rule_id": decision.rule_id,
            "tool_name": tool_call.tool_name,
            "tool_version": manifest.version,
            "permission_scope": tool_call.permission_scope,
            "side_effects": manifest.side_effects,
            "url": url_preview,
            "args_keys": sorted(args_dict.keys()),
            "header_keys": header_keys,
            "body": body_summaries or None,
        }

        sanitized = self.redactor.redact(preview_obj)
        lines = _format_preview_lines(sanitized, manifest=manifest)
        preview = "\n".join(lines)
        return truncate_str(preview, max_bytes=self.max_preview_bytes)


def _extract_url(parsed_args: BaseModel) -> str | None:
    preferred = ("url", "target_url", "webhook_url", "endpoint_url")
    for name in preferred:
        value = getattr(parsed_args, name, None)
        if isinstance(value, str):
            trimmed = value.strip()
            if trimmed:
                return trimmed

    for field_name in type(parsed_args).model_fields:
        if "url" not in field_name.lower():
            continue
        value = getattr(parsed_args, field_name, None)
        if isinstance(value, str):
            trimmed = value.strip()
            if trimmed:
                return trimmed

    return None


def _safe_url_preview(raw_url: str) -> str:
    trimmed = raw_url.strip()
    if not trimmed:
        return ""

    parts = urlsplit(trimmed)
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        return trimmed

    if parts.port is not None:
        host = f"{host}:{parts.port}"

    scheme = parts.scheme or "https"
    path = parts.path or "/"
    return f"{scheme}://{host}{path}"


def _summarize_body(value: object) -> dict[str, object]:
    data: bytes
    if value is None:
        data = b""
    elif isinstance(value, bytes):
        data = value
    elif isinstance(value, str):
        data = value.encode("utf-8")
    else:
        try:
            data = canonical_json(value).encode("utf-8")
        except TypeError:
            data = str(value).encode("utf-8")

    return {"sha256": stable_sha256(data), "bytes": len(data)}


def _format_preview_lines(
    preview_obj: object,
    *,
    manifest: ToolManifest,
) -> list[str]:
    if not isinstance(preview_obj, dict):
        return [f"tool_name: {manifest.name}", "preview: <unavailable>"]

    def get_str(key: str) -> str | None:
        value = preview_obj.get(key)
        if value is None:
            return None
        if isinstance(value, str):
            trimmed = value.strip()
            return trimmed or None
        return str(value)

    lines: list[str] = []
    for key in ("action", "reason_code", "rule_id"):
        value = get_str(key)
        if value is not None:
            lines.append(f"{key}: {value}")

    tool_name = get_str("tool_name") or manifest.name
    lines.append(f"tool_name: {tool_name}")

    tool_version = get_str("tool_version") or manifest.version
    lines.append(f"tool_version: {tool_version}")

    permission_scope = get_str("permission_scope") or manifest.permission_scope
    lines.append(f"permission_scope: {permission_scope}")

    side_effects = get_str("side_effects")
    lines.append(
        f"side_effects: {side_effects if side_effects is not None else manifest.side_effects}"
    )

    url_value = get_str("url")
    if url_value is not None:
        lines.append(f"url: {url_value}")

    args_keys = preview_obj.get("args_keys")
    if isinstance(args_keys, list):
        lines.append(f"args_keys: {canonical_json(args_keys)}")

    header_keys = preview_obj.get("header_keys")
    if isinstance(header_keys, list):
        lines.append(f"header_keys: {canonical_json(header_keys)}")

    body = preview_obj.get("body")
    if isinstance(body, dict) and body:
        lines.append(f"body: {canonical_json(body)}")

    return lines

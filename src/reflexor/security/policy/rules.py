"""Policy rules (composable).

Clean Architecture:
Policy rules may depend on:
- `reflexor.domain`
- `reflexor.config`
- `reflexor.security.*` utilities
- tool boundary contracts (`reflexor.tools.sdk`)

They must remain independent from infrastructure/framework concerns.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from pydantic import BaseModel

from reflexor.domain.models import ToolCall
from reflexor.security.fs_safety import resolve_path_in_workspace
from reflexor.security.net_safety import hostname_matches_allowlist, validate_and_normalize_url
from reflexor.security.policy.context import PolicyContext, ToolSpec
from reflexor.security.policy.decision import (
    REASON_APPROVAL_REQUIRED,
    REASON_ARGS_INVALID,
    REASON_DOMAIN_NOT_ALLOWLISTED,
    REASON_PROFILE_GUARDRAIL,
    REASON_SCOPE_DISABLED,
    REASON_SCOPE_MISMATCH,
    REASON_SSRF_BLOCKED,
    REASON_WORKSPACE_VIOLATION,
    PolicyAction,
    PolicyDecision,
)
from reflexor.security.scopes import Scope


class PolicyRule(Protocol):
    """A single policy rule that can allow/deny/require approval."""

    def evaluate(
        self,
        *,
        tool_call: ToolCall,
        tool_spec: ToolSpec,
        parsed_args: BaseModel,
        ctx: PolicyContext,
    ) -> PolicyDecision | None: ...


NETWORK_SCOPES: frozenset[str] = frozenset({Scope.NET_HTTP.value, Scope.WEBHOOK_EMIT.value})
FILESYSTEM_SCOPES: frozenset[str] = frozenset({Scope.FS_READ.value, Scope.FS_WRITE.value})


class ScopeMatchesManifestRule:
    """Deny if tool_call.permission_scope does not match the tool manifest.

    This prevents policy bypass if a persisted tool call is tampered with (or created incorrectly)
    such that its scope no longer matches the tool being executed.
    """

    rule_id = "scope_matches_manifest"

    def evaluate(
        self,
        *,
        tool_call: ToolCall,
        tool_spec: ToolSpec,
        parsed_args: BaseModel,
        ctx: PolicyContext,
    ) -> PolicyDecision | None:
        _ = parsed_args
        _ = ctx

        expected = tool_spec.manifest.permission_scope
        actual = tool_call.permission_scope
        if expected == actual:
            return None

        return PolicyDecision.deny(
            reason_code=REASON_SCOPE_MISMATCH,
            message="tool_call permission_scope does not match tool manifest",
            rule_id=self.rule_id,
            metadata={
                "tool_name": tool_spec.tool_name,
                "expected_scope": expected,
                "actual_scope": actual,
            },
        )


class ScopeEnabledRule:
    """Deny if the tool call's scope is not enabled."""

    rule_id = "scope_enabled"

    def evaluate(
        self,
        *,
        tool_call: ToolCall,
        tool_spec: ToolSpec,
        parsed_args: BaseModel,
        ctx: PolicyContext,
    ) -> PolicyDecision | None:
        _ = parsed_args
        scope = tool_call.permission_scope
        if scope in ctx.enabled_scopes:
            return None

        return PolicyDecision.deny(
            reason_code=REASON_SCOPE_DISABLED,
            message="scope is not enabled",
            rule_id=self.rule_id,
            metadata={"scope": scope, "tool_name": tool_spec.tool_name},
        )


class NetworkAllowlistRule:
    """Apply SSRF and allowlist checks for network-scoped tools."""

    rule_id = "network_allowlist"

    def evaluate(
        self,
        *,
        tool_call: ToolCall,
        tool_spec: ToolSpec,
        parsed_args: BaseModel,
        ctx: PolicyContext,
    ) -> PolicyDecision | None:
        scope = tool_call.permission_scope
        if scope not in NETWORK_SCOPES:
            return None

        raw_url = _extract_url(parsed_args)
        if raw_url is None:
            return PolicyDecision.deny(
                reason_code=REASON_ARGS_INVALID,
                message="network-scoped tools must include a URL in args",
                rule_id=self.rule_id,
                metadata={"scope": scope, "tool_name": tool_spec.tool_name},
            )

        try:
            if scope == Scope.NET_HTTP.value:
                normalized_url = validate_and_normalize_url(
                    raw_url,
                    allowed_domains=ctx.allowlists.http_allowed_domains,
                    require_https=True,
                )
            else:
                normalized_url = validate_and_normalize_url(raw_url, require_https=True)
        except ValueError as exc:
            message = str(exc)
            reason_code = (
                REASON_DOMAIN_NOT_ALLOWLISTED
                if "allowed_domains" in message
                else REASON_SSRF_BLOCKED
            )
            host = urlsplit(raw_url).hostname
            metadata: dict[str, object] = {"scope": scope, "tool_name": tool_spec.tool_name}
            if host:
                metadata["host"] = host.lower().rstrip(".")
            metadata["url"] = raw_url

            return PolicyDecision.deny(
                reason_code=reason_code,
                message=message,
                rule_id=self.rule_id,
                metadata=metadata,
            )

        if scope == Scope.WEBHOOK_EMIT.value:
            if normalized_url not in ctx.allowlists.webhook_allowed_targets:
                host = urlsplit(normalized_url).hostname
                metadata = {"scope": scope, "tool_name": tool_spec.tool_name, "url": normalized_url}
                if host:
                    metadata["host"] = host.lower().rstrip(".")
                return PolicyDecision.deny(
                    reason_code=REASON_DOMAIN_NOT_ALLOWLISTED,
                    message="webhook target is not allowlisted",
                    rule_id=self.rule_id,
                    metadata=metadata,
                )

        return None


class WorkspaceRule:
    """Validate that filesystem tool paths remain confined to workspace_root."""

    rule_id = "workspace_confinement"

    def evaluate(
        self,
        *,
        tool_call: ToolCall,
        tool_spec: ToolSpec,
        parsed_args: BaseModel,
        ctx: PolicyContext,
    ) -> PolicyDecision | None:
        scope = tool_call.permission_scope
        if scope not in FILESYSTEM_SCOPES:
            return None

        for field_name, path in _iter_candidate_paths(parsed_args):
            try:
                resolve_path_in_workspace(path, workspace_root=ctx.workspace_root, must_exist=False)
            except ValueError as exc:
                return PolicyDecision.deny(
                    reason_code=REASON_WORKSPACE_VIOLATION,
                    message=str(exc),
                    rule_id=self.rule_id,
                    metadata={
                        "scope": scope,
                        "tool_name": tool_spec.tool_name,
                        "field": field_name,
                        "path": str(path),
                    },
                )

        return None


class ApprovalRequiredRule:
    """Require approval for configured scopes and for unsafe prod side effects."""

    rule_id = "approval_required"

    def evaluate(
        self,
        *,
        tool_call: ToolCall,
        tool_spec: ToolSpec,
        parsed_args: BaseModel,
        ctx: PolicyContext,
    ) -> PolicyDecision | None:
        scope = tool_call.permission_scope

        if scope in ctx.approval_required_scopes:
            return PolicyDecision.require_approval(
                reason_code=REASON_APPROVAL_REQUIRED,
                message="scope requires approval",
                rule_id=self.rule_id,
                metadata={"scope": scope, "tool_name": tool_spec.tool_name},
            )

        raw_url = _extract_url(parsed_args)
        if raw_url is not None:
            host = urlsplit(raw_url).hostname
            if host and hostname_matches_allowlist(host, ctx.approval_required_domains):
                return PolicyDecision.require_approval(
                    reason_code=REASON_APPROVAL_REQUIRED,
                    message="destination domain requires approval",
                    rule_id=self.rule_id,
                    metadata={
                        "scope": scope,
                        "tool_name": tool_spec.tool_name,
                        "host": host.lower().rstrip("."),
                    },
                )

        if ctx.approval_required_payload_keywords:
            payload_text = json.dumps(
                parsed_args.model_dump(mode="json"),
                ensure_ascii=False,
            ).lower()
            for keyword in ctx.approval_required_payload_keywords:
                if keyword in payload_text:
                    return PolicyDecision.require_approval(
                        reason_code=REASON_APPROVAL_REQUIRED,
                        message="payload classification requires approval",
                        rule_id=self.rule_id,
                        metadata={
                            "scope": scope,
                            "tool_name": tool_spec.tool_name,
                            "keyword": keyword,
                        },
                    )

        if ctx.profile == "prod" and tool_spec.manifest.side_effects and not ctx.dry_run:
            return PolicyDecision.require_approval(
                reason_code=REASON_PROFILE_GUARDRAIL,
                message="side-effecting tools in prod require approval",
                rule_id=self.rule_id,
                metadata={
                    "profile": ctx.profile,
                    "dry_run": ctx.dry_run,
                    "scope": scope,
                    "tool_name": tool_spec.tool_name,
                },
            )

        return None


def evaluate_rules(
    rules: Sequence[PolicyRule],
    *,
    tool_call: ToolCall,
    tool_spec: ToolSpec,
    parsed_args: BaseModel,
    ctx: PolicyContext,
) -> PolicyDecision:
    """Evaluate rules in order, returning the first non-allow decision."""

    for rule in rules:
        decision = rule.evaluate(
            tool_call=tool_call,
            tool_spec=tool_spec,
            parsed_args=parsed_args,
            ctx=ctx,
        )
        if decision is None:
            continue
        if decision.action == PolicyAction.ALLOW:
            continue
        return decision
    return PolicyDecision.allow()


def _extract_url(args: BaseModel) -> str | None:
    preferred = ("url", "target_url", "webhook_url", "endpoint_url")
    for name in preferred:
        value = getattr(args, name, None)
        if isinstance(value, str):
            trimmed = value.strip()
            if trimmed:
                return trimmed

    for field_name in type(args).model_fields:
        if "url" not in field_name.lower():
            continue
        value = getattr(args, field_name)
        if isinstance(value, str):
            trimmed = value.strip()
            if trimmed:
                return trimmed
    return None


def _iter_candidate_paths(args: BaseModel) -> Iterator[tuple[str, Path]]:
    for field_name in type(args).model_fields:
        value = getattr(args, field_name)
        yield from _iter_paths_for_field(field_name, value)


def _iter_paths_for_field(field_name: str, value: object) -> Iterator[tuple[str, Path]]:
    if isinstance(value, Path):
        yield (field_name, value)
        return

    if isinstance(value, BaseModel):
        yield from _iter_candidate_paths(value)
        return

    if isinstance(value, str) and "path" in field_name.lower():
        trimmed = value.strip()
        if trimmed:
            yield (field_name, Path(trimmed))
        return

    if isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_paths_for_field(field_name, item)
        return

    if isinstance(value, dict):
        for item in value.values():
            yield from _iter_paths_for_field(field_name, item)
        return

    return

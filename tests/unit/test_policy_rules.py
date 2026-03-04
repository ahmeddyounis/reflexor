from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from reflexor.config import ReflexorSettings
from reflexor.domain.models import ToolCall
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
)
from reflexor.security.policy.rules import (
    ApprovalRequiredRule,
    NetworkAllowlistRule,
    ScopeEnabledRule,
    ScopeMatchesManifestRule,
    WorkspaceRule,
)
from reflexor.tools.sdk import ToolManifest


class UrlArgs(BaseModel):
    url: str


class PathArgs(BaseModel):
    path: Path


def _tool_call(*, tool_name: str, scope: str) -> ToolCall:
    return ToolCall(
        tool_name=tool_name,
        permission_scope=scope,
        idempotency_key="k",
        args={},
    )


def _tool_spec(*, manifest: ToolManifest) -> ToolSpec:
    return ToolSpec(tool_name=manifest.name, manifest=manifest, args_model=UrlArgs)


def test_scope_enabled_rule_denies_when_scope_disabled(tmp_path: Path) -> None:
    settings = ReflexorSettings(workspace_root=tmp_path, enabled_scopes=["fs.read"])
    ctx = PolicyContext.from_settings(settings)

    manifest = ToolManifest(
        name="tests.fs",
        version="0.1.0",
        description="fs tool",
        permission_scope="fs.write",
        idempotent=True,
    )
    tool_spec = ToolSpec(tool_name=manifest.name, manifest=manifest, args_model=PathArgs)

    rule = ScopeEnabledRule()
    decision = rule.evaluate(
        tool_call=_tool_call(tool_name=manifest.name, scope="fs.write"),
        tool_spec=tool_spec,
        parsed_args=PathArgs(path=Path("x.txt")),
        ctx=ctx,
    )

    assert decision is not None
    assert decision.action == PolicyAction.DENY
    assert decision.reason_code == REASON_SCOPE_DISABLED
    assert decision.metadata["scope"] == "fs.write"


def test_scope_matches_manifest_rule_denies_when_scope_mismatched(tmp_path: Path) -> None:
    settings = ReflexorSettings(workspace_root=tmp_path, enabled_scopes=["fs.read", "net.http"])
    ctx = PolicyContext.from_settings(settings)

    manifest = ToolManifest(
        name="tests.http",
        version="0.1.0",
        description="http tool",
        permission_scope="net.http",
        idempotent=True,
    )
    tool_spec = ToolSpec(tool_name=manifest.name, manifest=manifest, args_model=UrlArgs)

    rule = ScopeMatchesManifestRule()
    decision = rule.evaluate(
        tool_call=_tool_call(tool_name=manifest.name, scope="fs.read"),
        tool_spec=tool_spec,
        parsed_args=UrlArgs(url="https://example.com/"),
        ctx=ctx,
    )

    assert decision is not None
    assert decision.action == PolicyAction.DENY
    assert decision.reason_code == REASON_SCOPE_MISMATCH
    assert decision.metadata["expected_scope"] == "net.http"
    assert decision.metadata["actual_scope"] == "fs.read"


def test_scope_matches_manifest_rule_allows_when_scope_matches(tmp_path: Path) -> None:
    settings = ReflexorSettings(workspace_root=tmp_path, enabled_scopes=["fs.read", "net.http"])
    ctx = PolicyContext.from_settings(settings)

    manifest = ToolManifest(
        name="tests.http",
        version="0.1.0",
        description="http tool",
        permission_scope="net.http",
        idempotent=True,
    )
    tool_spec = ToolSpec(tool_name=manifest.name, manifest=manifest, args_model=UrlArgs)

    rule = ScopeMatchesManifestRule()
    decision = rule.evaluate(
        tool_call=_tool_call(tool_name=manifest.name, scope="net.http"),
        tool_spec=tool_spec,
        parsed_args=UrlArgs(url="https://example.com/"),
        ctx=ctx,
    )
    assert decision is None


def test_scope_enabled_rule_allows_when_scope_enabled(tmp_path: Path) -> None:
    settings = ReflexorSettings(workspace_root=tmp_path, enabled_scopes=["fs.read"])
    ctx = PolicyContext.from_settings(settings)

    manifest = ToolManifest(
        name="tests.fs",
        version="0.1.0",
        description="fs tool",
        permission_scope="fs.read",
        idempotent=True,
    )
    tool_spec = ToolSpec(tool_name=manifest.name, manifest=manifest, args_model=PathArgs)

    rule = ScopeEnabledRule()
    decision = rule.evaluate(
        tool_call=_tool_call(tool_name=manifest.name, scope="fs.read"),
        tool_spec=tool_spec,
        parsed_args=PathArgs(path=Path("x.txt")),
        ctx=ctx,
    )
    assert decision is None


def test_network_allowlist_rule_allows_allowlisted_domain(tmp_path: Path) -> None:
    settings = ReflexorSettings(
        workspace_root=tmp_path, enabled_scopes=["net.http"], http_allowed_domains=["example.com"]
    )
    ctx = PolicyContext.from_settings(settings)

    manifest = ToolManifest(
        name="tests.http",
        version="0.1.0",
        description="http tool",
        permission_scope="net.http",
        idempotent=True,
    )
    tool_spec = ToolSpec(tool_name=manifest.name, manifest=manifest, args_model=UrlArgs)

    rule = NetworkAllowlistRule()
    decision = rule.evaluate(
        tool_call=_tool_call(tool_name=manifest.name, scope="net.http"),
        tool_spec=tool_spec,
        parsed_args=UrlArgs(url=" https://Example.com/Path "),
        ctx=ctx,
    )
    assert decision is None


def test_network_allowlist_rule_denies_unallowlisted_domain(tmp_path: Path) -> None:
    settings = ReflexorSettings(
        workspace_root=tmp_path, enabled_scopes=["net.http"], http_allowed_domains=["example.com"]
    )
    ctx = PolicyContext.from_settings(settings)

    manifest = ToolManifest(
        name="tests.http",
        version="0.1.0",
        description="http tool",
        permission_scope="net.http",
        idempotent=True,
    )
    tool_spec = ToolSpec(tool_name=manifest.name, manifest=manifest, args_model=UrlArgs)

    rule = NetworkAllowlistRule()
    decision = rule.evaluate(
        tool_call=_tool_call(tool_name=manifest.name, scope="net.http"),
        tool_spec=tool_spec,
        parsed_args=UrlArgs(url="https://evil.example/"),
        ctx=ctx,
    )

    assert decision is not None
    assert decision.action == PolicyAction.DENY
    assert decision.reason_code == REASON_DOMAIN_NOT_ALLOWLISTED
    assert decision.metadata["host"] == "evil.example"
    assert decision.metadata["scope"] == "net.http"


def test_network_allowlist_rule_blocks_ip_literals(tmp_path: Path) -> None:
    settings = ReflexorSettings(
        workspace_root=tmp_path, enabled_scopes=["net.http"], http_allowed_domains=["example.com"]
    )
    ctx = PolicyContext.from_settings(settings)

    manifest = ToolManifest(
        name="tests.http",
        version="0.1.0",
        description="http tool",
        permission_scope="net.http",
        idempotent=True,
    )
    tool_spec = ToolSpec(tool_name=manifest.name, manifest=manifest, args_model=UrlArgs)

    rule = NetworkAllowlistRule()
    decision = rule.evaluate(
        tool_call=_tool_call(tool_name=manifest.name, scope="net.http"),
        tool_spec=tool_spec,
        parsed_args=UrlArgs(url="https://127.0.0.1/"),
        ctx=ctx,
    )

    assert decision is not None
    assert decision.action == PolicyAction.DENY
    assert decision.reason_code == REASON_SSRF_BLOCKED


def test_network_allowlist_rule_denies_missing_url_args(tmp_path: Path) -> None:
    settings = ReflexorSettings(
        workspace_root=tmp_path,
        enabled_scopes=["net.http"],
        http_allowed_domains=["example.com"],
    )
    ctx = PolicyContext.from_settings(settings)

    manifest = ToolManifest(
        name="tests.http",
        version="0.1.0",
        description="http tool",
        permission_scope="net.http",
        idempotent=True,
    )
    tool_spec = ToolSpec(tool_name=manifest.name, manifest=manifest, args_model=PathArgs)

    rule = NetworkAllowlistRule()
    decision = rule.evaluate(
        tool_call=_tool_call(tool_name=manifest.name, scope="net.http"),
        tool_spec=tool_spec,
        parsed_args=PathArgs(path=Path("x.txt")),
        ctx=ctx,
    )

    assert decision is not None
    assert decision.action == PolicyAction.DENY
    assert decision.reason_code == REASON_ARGS_INVALID
    assert decision.metadata["scope"] == "net.http"


def test_network_allowlist_rule_allows_allowlisted_webhook_target(tmp_path: Path) -> None:
    settings = ReflexorSettings(
        workspace_root=tmp_path,
        enabled_scopes=["webhook.emit"],
        webhook_allowed_targets=["https://hooks.example.com/path"],
    )
    ctx = PolicyContext.from_settings(settings)

    manifest = ToolManifest(
        name="tests.webhook",
        version="0.1.0",
        description="webhook tool",
        permission_scope="webhook.emit",
        idempotent=True,
    )
    tool_spec = ToolSpec(tool_name=manifest.name, manifest=manifest, args_model=UrlArgs)

    rule = NetworkAllowlistRule()
    decision = rule.evaluate(
        tool_call=_tool_call(tool_name=manifest.name, scope="webhook.emit"),
        tool_spec=tool_spec,
        parsed_args=UrlArgs(url=" https://Hooks.Example.com/path "),
        ctx=ctx,
    )
    assert decision is None


def test_network_allowlist_rule_denies_unallowlisted_webhook_target(tmp_path: Path) -> None:
    settings = ReflexorSettings(
        workspace_root=tmp_path,
        enabled_scopes=["webhook.emit"],
        webhook_allowed_targets=["https://hooks.example.com/path"],
    )
    ctx = PolicyContext.from_settings(settings)

    manifest = ToolManifest(
        name="tests.webhook",
        version="0.1.0",
        description="webhook tool",
        permission_scope="webhook.emit",
        idempotent=True,
    )
    tool_spec = ToolSpec(tool_name=manifest.name, manifest=manifest, args_model=UrlArgs)

    rule = NetworkAllowlistRule()
    decision = rule.evaluate(
        tool_call=_tool_call(tool_name=manifest.name, scope="webhook.emit"),
        tool_spec=tool_spec,
        parsed_args=UrlArgs(url="https://hooks.example.com/other"),
        ctx=ctx,
    )

    assert decision is not None
    assert decision.action == PolicyAction.DENY
    assert decision.reason_code == REASON_DOMAIN_NOT_ALLOWLISTED
    assert decision.metadata["host"] == "hooks.example.com"
    assert decision.metadata["scope"] == "webhook.emit"


def test_workspace_rule_denies_paths_outside_workspace(tmp_path: Path) -> None:
    settings = ReflexorSettings(workspace_root=tmp_path, enabled_scopes=["fs.write"])
    ctx = PolicyContext.from_settings(settings)

    manifest = ToolManifest(
        name="tests.fs",
        version="0.1.0",
        description="fs tool",
        permission_scope="fs.write",
        idempotent=True,
    )
    tool_spec = ToolSpec(tool_name=manifest.name, manifest=manifest, args_model=PathArgs)

    rule = WorkspaceRule()
    decision = rule.evaluate(
        tool_call=_tool_call(tool_name=manifest.name, scope="fs.write"),
        tool_spec=tool_spec,
        parsed_args=PathArgs(path=Path("../escape.txt")),
        ctx=ctx,
    )

    assert decision is not None
    assert decision.action == PolicyAction.DENY
    assert decision.reason_code == REASON_WORKSPACE_VIOLATION
    assert decision.metadata["scope"] == "fs.write"


def test_workspace_rule_allows_paths_inside_workspace(tmp_path: Path) -> None:
    settings = ReflexorSettings(workspace_root=tmp_path, enabled_scopes=["fs.write"])
    ctx = PolicyContext.from_settings(settings)

    manifest = ToolManifest(
        name="tests.fs",
        version="0.1.0",
        description="fs tool",
        permission_scope="fs.write",
        idempotent=True,
    )
    tool_spec = ToolSpec(tool_name=manifest.name, manifest=manifest, args_model=PathArgs)

    rule = WorkspaceRule()
    decision = rule.evaluate(
        tool_call=_tool_call(tool_name=manifest.name, scope="fs.write"),
        tool_spec=tool_spec,
        parsed_args=PathArgs(path=Path("subdir/file.txt")),
        ctx=ctx,
    )
    assert decision is None


def test_approval_required_rule_requires_approval_for_scopes(tmp_path: Path) -> None:
    settings = ReflexorSettings(
        workspace_root=tmp_path,
        enabled_scopes=["fs.write"],
        approval_required_scopes=["fs.write"],
    )
    ctx = PolicyContext.from_settings(settings)

    manifest = ToolManifest(
        name="tests.fs",
        version="0.1.0",
        description="fs tool",
        permission_scope="fs.write",
        idempotent=True,
    )
    tool_spec = ToolSpec(tool_name=manifest.name, manifest=manifest, args_model=PathArgs)

    rule = ApprovalRequiredRule()
    decision = rule.evaluate(
        tool_call=_tool_call(tool_name=manifest.name, scope="fs.write"),
        tool_spec=tool_spec,
        parsed_args=PathArgs(path=Path("file.txt")),
        ctx=ctx,
    )

    assert decision is not None
    assert decision.action == PolicyAction.REQUIRE_APPROVAL
    assert decision.reason_code == REASON_APPROVAL_REQUIRED


def test_approval_required_rule_requires_approval_for_prod_side_effects(tmp_path: Path) -> None:
    settings = ReflexorSettings(
        profile="prod",
        dry_run=False,
        allow_side_effects_in_prod=True,
        workspace_root=tmp_path,
        enabled_scopes=["fs.write"],
    )
    ctx = PolicyContext.from_settings(settings)

    manifest = ToolManifest(
        name="tests.side_effect",
        version="0.1.0",
        description="side effect tool",
        permission_scope="fs.write",
        side_effects=True,
        idempotent=False,
    )
    tool_spec = ToolSpec(tool_name=manifest.name, manifest=manifest, args_model=PathArgs)

    rule = ApprovalRequiredRule()
    decision = rule.evaluate(
        tool_call=_tool_call(tool_name=manifest.name, scope="fs.write"),
        tool_spec=tool_spec,
        parsed_args=PathArgs(path=Path("file.txt")),
        ctx=ctx,
    )

    assert decision is not None
    assert decision.action == PolicyAction.REQUIRE_APPROVAL
    assert decision.reason_code == REASON_PROFILE_GUARDRAIL


def test_approval_required_rule_does_not_require_approval_for_dry_run(tmp_path: Path) -> None:
    settings = ReflexorSettings(
        profile="prod",
        dry_run=True,
        workspace_root=tmp_path,
        enabled_scopes=["fs.write"],
    )
    ctx = PolicyContext.from_settings(settings)

    manifest = ToolManifest(
        name="tests.side_effect",
        version="0.1.0",
        description="side effect tool",
        permission_scope="fs.write",
        side_effects=True,
        idempotent=False,
    )
    tool_spec = ToolSpec(tool_name=manifest.name, manifest=manifest, args_model=PathArgs)

    rule = ApprovalRequiredRule()
    decision = rule.evaluate(
        tool_call=_tool_call(tool_name=manifest.name, scope="fs.write"),
        tool_spec=tool_spec,
        parsed_args=PathArgs(path=Path("file.txt")),
        ctx=ctx,
    )

    assert decision is None

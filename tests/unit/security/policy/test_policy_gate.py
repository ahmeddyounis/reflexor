from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from reflexor.config import ReflexorSettings
from reflexor.domain.models import ToolCall
from reflexor.security.policy.context import ToolSpec
from reflexor.security.policy.decision import (
    REASON_ARGS_INVALID,
    REASON_DOMAIN_NOT_ALLOWLISTED,
    REASON_PROFILE_GUARDRAIL,
    REASON_SCOPE_DISABLED,
    REASON_SSRF_BLOCKED,
    REASON_WORKSPACE_VIOLATION,
    PolicyAction,
)
from reflexor.security.policy.gate import PolicyGate
from reflexor.security.policy.rules import (
    ApprovalRequiredRule,
    NetworkAllowlistRule,
    ScopeEnabledRule,
    WorkspaceRule,
)
from reflexor.tools.sdk import ToolManifest


class UrlArgs(BaseModel):
    url: str


class NoUrlArgs(BaseModel):
    headers: dict[str, str] | None = None


class PathArgs(BaseModel):
    path: Path


def _gate(*, settings: ReflexorSettings) -> PolicyGate:
    return PolicyGate(
        rules=[ScopeEnabledRule(), NetworkAllowlistRule(), WorkspaceRule(), ApprovalRequiredRule()],
        settings=settings,
    )


def _tool_call(*, tool_name: str, scope: str) -> ToolCall:
    return ToolCall(
        tool_name=tool_name,
        permission_scope=scope,
        idempotency_key="k",
        args={},
    )


def _tool_spec(
    *,
    tool_name: str,
    scope: str,
    side_effects: bool,
    args_model: type[BaseModel],
) -> ToolSpec:
    manifest = ToolManifest(
        name=tool_name,
        version="0.1.0",
        description="tool",
        permission_scope=scope,
        side_effects=side_effects,
        idempotent=True,
    )
    return ToolSpec(tool_name=tool_name, manifest=manifest, args_model=args_model)


def _assert_json_safe_decision(decision: object) -> None:
    from reflexor.security.policy.decision import PolicyDecision

    assert isinstance(decision, PolicyDecision)
    payload = decision.model_dump(mode="json")
    json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def test_scope_disabled_denies_with_metadata(tmp_path: Path) -> None:
    settings = ReflexorSettings(workspace_root=tmp_path, enabled_scopes=["fs.read"])
    gate = _gate(settings=settings)

    tool_name = "tests.fs"
    tool_spec = _tool_spec(
        tool_name=tool_name, scope="fs.write", side_effects=False, args_model=PathArgs
    )

    decision = gate.evaluate(
        tool_call=_tool_call(tool_name=tool_name, scope="fs.write"),
        tool_spec=tool_spec,
        parsed_args=PathArgs(path=Path("file.txt")),
    )

    _assert_json_safe_decision(decision)
    assert decision.action == PolicyAction.DENY
    assert decision.reason_code == REASON_SCOPE_DISABLED
    assert decision.rule_id == ScopeEnabledRule.rule_id
    assert decision.metadata["scope"] == "fs.write"
    assert decision.metadata["tool_name"] == tool_name


@pytest.mark.parametrize(
    ("args_model", "parsed_args", "expected_reason"),
    [
        (NoUrlArgs, NoUrlArgs(), REASON_ARGS_INVALID),
        (UrlArgs, UrlArgs(url="   "), REASON_ARGS_INVALID),
    ],
)
def test_network_rule_denies_when_url_missing_or_empty(
    tmp_path: Path,
    args_model: type[BaseModel],
    parsed_args: BaseModel,
    expected_reason: str,
) -> None:
    settings = ReflexorSettings(workspace_root=tmp_path, enabled_scopes=["net.http"])
    gate = _gate(settings=settings)

    tool_name = "tests.http"
    tool_spec = _tool_spec(
        tool_name=tool_name, scope="net.http", side_effects=False, args_model=args_model
    )

    decision = gate.evaluate(
        tool_call=_tool_call(tool_name=tool_name, scope="net.http"),
        tool_spec=tool_spec,
        parsed_args=parsed_args,
    )

    _assert_json_safe_decision(decision)
    assert decision.action == PolicyAction.DENY
    assert decision.reason_code == expected_reason
    assert decision.rule_id == NetworkAllowlistRule.rule_id
    assert decision.metadata["scope"] == "net.http"
    assert decision.metadata["tool_name"] == tool_name


def test_network_rule_denies_when_allowlist_missing_or_empty(tmp_path: Path) -> None:
    settings = ReflexorSettings(workspace_root=tmp_path, enabled_scopes=["net.http"])
    gate = _gate(settings=settings)

    tool_name = "tests.http"
    tool_spec = _tool_spec(
        tool_name=tool_name, scope="net.http", side_effects=False, args_model=UrlArgs
    )

    decision = gate.evaluate(
        tool_call=_tool_call(tool_name=tool_name, scope="net.http"),
        tool_spec=tool_spec,
        parsed_args=UrlArgs(url="https://example.com/path"),
    )

    _assert_json_safe_decision(decision)
    assert decision.action == PolicyAction.DENY
    assert decision.reason_code == REASON_DOMAIN_NOT_ALLOWLISTED
    assert decision.rule_id == NetworkAllowlistRule.rule_id
    assert decision.metadata["scope"] == "net.http"
    assert decision.metadata["tool_name"] == tool_name
    assert decision.metadata["host"] == "example.com"
    assert decision.metadata["url"] == "https://example.com/path"


@pytest.mark.parametrize(
    ("url", "expected_host"),
    [
        ("https://127.0.0.1/", "127.0.0.1"),
        ("https://10.0.0.1/", "10.0.0.1"),
        ("https://169.254.169.254/latest/meta-data/", "169.254.169.254"),
    ],
)
def test_network_rule_blocks_ssrf_like_targets(
    tmp_path: Path, url: str, expected_host: str
) -> None:
    settings = ReflexorSettings(
        workspace_root=tmp_path,
        enabled_scopes=["net.http"],
        http_allowed_domains=["example.com"],
    )
    gate = _gate(settings=settings)

    tool_name = "tests.http"
    tool_spec = _tool_spec(
        tool_name=tool_name, scope="net.http", side_effects=False, args_model=UrlArgs
    )

    decision = gate.evaluate(
        tool_call=_tool_call(tool_name=tool_name, scope="net.http"),
        tool_spec=tool_spec,
        parsed_args=UrlArgs(url=url),
    )

    _assert_json_safe_decision(decision)
    assert decision.action == PolicyAction.DENY
    assert decision.reason_code == REASON_SSRF_BLOCKED
    assert decision.rule_id == NetworkAllowlistRule.rule_id
    assert decision.metadata["scope"] == "net.http"
    assert decision.metadata["tool_name"] == tool_name
    assert decision.metadata["host"] == expected_host
    assert decision.metadata["url"] == url


def test_workspace_escape_denies_with_field_metadata(tmp_path: Path) -> None:
    settings = ReflexorSettings(workspace_root=tmp_path, enabled_scopes=["fs.write"])
    gate = _gate(settings=settings)

    tool_name = "tests.fs"
    tool_spec = _tool_spec(
        tool_name=tool_name, scope="fs.write", side_effects=False, args_model=PathArgs
    )

    decision = gate.evaluate(
        tool_call=_tool_call(tool_name=tool_name, scope="fs.write"),
        tool_spec=tool_spec,
        parsed_args=PathArgs(path=Path("../escape.txt")),
    )

    _assert_json_safe_decision(decision)
    assert decision.action == PolicyAction.DENY
    assert decision.reason_code == REASON_WORKSPACE_VIOLATION
    assert decision.rule_id == WorkspaceRule.rule_id
    assert decision.metadata["scope"] == "fs.write"
    assert decision.metadata["tool_name"] == tool_name
    assert decision.metadata["field"] == "path"
    assert decision.metadata["path"] == "../escape.txt"


def test_prod_side_effects_require_approval_when_not_dry_run(tmp_path: Path) -> None:
    settings = ReflexorSettings(
        profile="prod",
        dry_run=False,
        allow_side_effects_in_prod=True,
        workspace_root=tmp_path,
        enabled_scopes=["fs.write"],
    )
    gate = _gate(settings=settings)

    tool_name = "tests.side_effect"
    tool_spec = _tool_spec(
        tool_name=tool_name, scope="fs.write", side_effects=True, args_model=PathArgs
    )

    decision = gate.evaluate(
        tool_call=_tool_call(tool_name=tool_name, scope="fs.write"),
        tool_spec=tool_spec,
        parsed_args=PathArgs(path=Path("file.txt")),
    )

    _assert_json_safe_decision(decision)
    assert decision.action == PolicyAction.REQUIRE_APPROVAL
    assert decision.reason_code == REASON_PROFILE_GUARDRAIL
    assert decision.rule_id == ApprovalRequiredRule.rule_id
    assert decision.metadata["profile"] == "prod"
    assert decision.metadata["dry_run"] is False
    assert decision.metadata["scope"] == "fs.write"
    assert decision.metadata["tool_name"] == tool_name


def test_dev_profile_is_not_permissive_without_scopes_and_allowlists(tmp_path: Path) -> None:
    settings = ReflexorSettings(
        profile="dev",
        dry_run=True,
        workspace_root=tmp_path,
        enabled_scopes=["net.http"],
        http_allowed_domains=["example.com"],
    )
    gate = _gate(settings=settings)

    tool_name = "tests.http"
    tool_spec = _tool_spec(
        tool_name=tool_name, scope="net.http", side_effects=False, args_model=UrlArgs
    )

    decision = gate.evaluate(
        tool_call=_tool_call(tool_name=tool_name, scope="net.http"),
        tool_spec=tool_spec,
        parsed_args=UrlArgs(url="https://example.com/path"),
    )

    _assert_json_safe_decision(decision)
    assert decision.action == PolicyAction.ALLOW

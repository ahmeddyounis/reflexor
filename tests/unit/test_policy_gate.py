from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from reflexor.config import ReflexorSettings
from reflexor.domain.models import ToolCall
from reflexor.security.policy.context import PolicyContext, ToolSpec
from reflexor.security.policy.decision import (
    REASON_APPROVAL_REQUIRED,
    REASON_SCOPE_DISABLED,
    PolicyAction,
    PolicyDecision,
)
from reflexor.security.policy.gate import PolicyEvaluation, PolicyGate
from reflexor.tools.sdk import ToolManifest


class DummyArgs(BaseModel):
    path: Path = Path("file.txt")


class AlwaysRequireApprovalRule:
    rule_id = "tests.require_approval"

    def evaluate(
        self,
        *,
        tool_call: ToolCall,
        tool_spec: ToolSpec,
        parsed_args: BaseModel,
        ctx: PolicyContext,
    ) -> PolicyDecision:
        _ = (tool_call, tool_spec, parsed_args, ctx)
        return PolicyDecision.require_approval(
            reason_code=REASON_APPROVAL_REQUIRED,
            message="needs approval",
            rule_id=self.rule_id,
            metadata={"rule": self.rule_id},
        )


class AlwaysDenyRule:
    rule_id = "tests.deny"

    def evaluate(
        self,
        *,
        tool_call: ToolCall,
        tool_spec: ToolSpec,
        parsed_args: BaseModel,
        ctx: PolicyContext,
    ) -> PolicyDecision:
        _ = (tool_call, tool_spec, parsed_args, ctx)
        return PolicyDecision.deny(
            reason_code=REASON_SCOPE_DISABLED,
            message="denied",
            rule_id=self.rule_id,
            metadata={"rule": self.rule_id},
        )


class NoopRule:
    rule_id = "tests.noop"

    def evaluate(
        self,
        *,
        tool_call: ToolCall,
        tool_spec: ToolSpec,
        parsed_args: BaseModel,
        ctx: PolicyContext,
    ) -> None:
        _ = (tool_call, tool_spec, parsed_args, ctx)
        return None


def _tool_call(*, tool_name: str, scope: str) -> ToolCall:
    return ToolCall(
        tool_name=tool_name,
        permission_scope=scope,
        idempotency_key="k",
        args={},
    )


def _tool_spec(*, manifest: ToolManifest) -> ToolSpec:
    return ToolSpec(tool_name=manifest.name, manifest=manifest, args_model=DummyArgs)


def test_policy_gate_precedence_deny_beats_require_approval(tmp_path: Path) -> None:
    settings = ReflexorSettings(workspace_root=tmp_path, enabled_scopes=["fs.read"])
    ctx = PolicyContext.from_settings(settings)

    manifest = ToolManifest(
        name="tests.tool",
        version="0.1.0",
        description="tool",
        permission_scope="fs.read",
        idempotent=True,
    )
    tool_spec = _tool_spec(manifest=manifest)
    tool_call = _tool_call(tool_name=manifest.name, scope=manifest.permission_scope)

    gate = PolicyGate(rules=[AlwaysRequireApprovalRule(), AlwaysDenyRule()], settings=settings)
    decision = gate.evaluate(
        tool_call=tool_call, tool_spec=tool_spec, parsed_args=DummyArgs(), ctx=ctx
    )

    assert decision.action == PolicyAction.DENY
    assert decision.rule_id == AlwaysDenyRule.rule_id


def test_policy_gate_defaults_to_allow_when_no_rules_apply(tmp_path: Path) -> None:
    settings = ReflexorSettings(workspace_root=tmp_path, enabled_scopes=["fs.read"])
    ctx = PolicyContext.from_settings(settings)

    manifest = ToolManifest(
        name="tests.tool",
        version="0.1.0",
        description="tool",
        permission_scope="fs.read",
        idempotent=True,
    )
    tool_spec = _tool_spec(manifest=manifest)
    tool_call = _tool_call(tool_name=manifest.name, scope=manifest.permission_scope)

    gate = PolicyGate(rules=[NoopRule()], settings=settings)
    decision = gate.evaluate(
        tool_call=tool_call, tool_spec=tool_spec, parsed_args=DummyArgs(), ctx=ctx
    )

    assert decision.action == PolicyAction.ALLOW


def test_policy_gate_can_return_trace_and_is_json_safe(tmp_path: Path) -> None:
    settings = ReflexorSettings(workspace_root=tmp_path, enabled_scopes=["fs.read"])
    ctx = PolicyContext.from_settings(settings)

    manifest = ToolManifest(
        name="tests.tool",
        version="0.1.0",
        description="tool",
        permission_scope="fs.read",
        idempotent=True,
    )
    tool_spec = _tool_spec(manifest=manifest)
    tool_call = _tool_call(tool_name=manifest.name, scope=manifest.permission_scope)

    gate = PolicyGate(rules=[AlwaysRequireApprovalRule(), AlwaysDenyRule()], settings=settings)
    evaluation = gate.evaluate(
        tool_call=tool_call,
        tool_spec=tool_spec,
        parsed_args=DummyArgs(),
        ctx=ctx,
        policy_trace=True,
    )

    assert isinstance(evaluation, PolicyEvaluation)
    assert evaluation.decision.action == PolicyAction.DENY
    assert len(evaluation.trace) == 2
    assert evaluation.trace[0].rule_id == AlwaysRequireApprovalRule.rule_id
    assert evaluation.trace[0].decision is not None
    assert evaluation.trace[0].decision.action == PolicyAction.REQUIRE_APPROVAL
    assert evaluation.trace[1].rule_id == AlwaysDenyRule.rule_id
    assert evaluation.trace[1].decision is not None
    assert evaluation.trace[1].decision.action == PolicyAction.DENY

    payload = evaluation.model_dump(mode="json")
    json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def test_policy_gate_is_deterministic(tmp_path: Path) -> None:
    settings = ReflexorSettings(workspace_root=tmp_path, enabled_scopes=["fs.read"])
    ctx = PolicyContext.from_settings(settings)

    manifest = ToolManifest(
        name="tests.tool",
        version="0.1.0",
        description="tool",
        permission_scope="fs.read",
        idempotent=True,
    )
    tool_spec = _tool_spec(manifest=manifest)
    tool_call = _tool_call(tool_name=manifest.name, scope=manifest.permission_scope)

    gate = PolicyGate(rules=[AlwaysRequireApprovalRule(), AlwaysDenyRule()], settings=settings)

    first = gate.evaluate(
        tool_call=tool_call,
        tool_spec=tool_spec,
        parsed_args=DummyArgs(),
        ctx=ctx,
        policy_trace=True,
    )
    second = gate.evaluate(
        tool_call=tool_call,
        tool_spec=tool_spec,
        parsed_args=DummyArgs(),
        ctx=ctx,
        policy_trace=True,
    )
    assert first == second

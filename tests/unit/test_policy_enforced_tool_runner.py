from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import BaseModel

from reflexor.config import ReflexorSettings
from reflexor.domain.enums import ApprovalStatus
from reflexor.domain.models import Approval, ToolCall
from reflexor.security.policy.approvals import ApprovalBuilder, InMemoryApprovalStore
from reflexor.security.policy.decision import (
    REASON_ARGS_INVALID,
    REASON_SCOPE_DISABLED,
    REASON_TOOL_UNKNOWN,
    PolicyAction,
)
from reflexor.security.policy.enforcement import (
    APPROVAL_REQUIRED_ERROR_CODE,
    POLICY_DENIED_ERROR_CODE,
    PolicyEnforcedToolRunner,
)
from reflexor.security.policy.gate import PolicyGate
from reflexor.security.policy.rules import ApprovalRequiredRule, ScopeEnabledRule
from reflexor.tools.registry import ToolRegistry
from reflexor.tools.runner import ToolRunner
from reflexor.tools.sdk import ToolContext, ToolManifest, ToolResult


class CountArgs(BaseModel):
    count: int


class CountingTool:
    manifest = ToolManifest(
        name="tests.counting",
        version="0.1.0",
        description="Tool for policy-enforced runner tests.",
        permission_scope="fs.read",
        idempotent=True,
        max_output_bytes=10_000,
    )
    ArgsModel = CountArgs

    def __init__(self, counter: list[int]) -> None:
        self._counter = counter

    async def run(self, args: CountArgs, ctx: ToolContext) -> ToolResult:
        _ = ctx
        self._counter[0] += 1
        return ToolResult(ok=True, data={"count": args.count})


def _tool_call(*, tool_name: str, scope: str, args: dict[str, object]) -> ToolCall:
    return ToolCall(
        tool_name=tool_name,
        permission_scope=scope,
        idempotency_key="k",
        args=args,
    )


@pytest.mark.asyncio
async def test_unknown_tool_is_denied_without_execution(tmp_path: Path) -> None:
    settings = ReflexorSettings(workspace_root=tmp_path)
    registry = ToolRegistry()
    runner = ToolRunner(registry=registry, settings=settings)
    gate = PolicyGate(rules=[ScopeEnabledRule()], settings=settings)
    approvals = InMemoryApprovalStore()
    enforced = PolicyEnforcedToolRunner(
        registry=registry,
        runner=runner,
        gate=gate,
        approvals=approvals,
    )

    ctx = ToolContext(workspace_root=tmp_path, timeout_s=1.0)
    outcome = await enforced.execute_tool_call(
        _tool_call(tool_name="missing.tool", scope="fs.read", args={}),
        ctx=ctx,
    )

    assert outcome.decision.action == PolicyAction.DENY
    assert outcome.decision.reason_code == REASON_TOOL_UNKNOWN
    assert outcome.result.ok is False
    assert outcome.result.error_code == POLICY_DENIED_ERROR_CODE


@pytest.mark.asyncio
async def test_invalid_args_are_denied_without_execution(tmp_path: Path) -> None:
    settings = ReflexorSettings(workspace_root=tmp_path, enabled_scopes=["fs.read"])
    counter = [0]

    registry = ToolRegistry()
    registry.register(CountingTool(counter))
    runner = ToolRunner(registry=registry, settings=settings)
    gate = PolicyGate(rules=[ScopeEnabledRule()], settings=settings)
    approvals = InMemoryApprovalStore()
    enforced = PolicyEnforcedToolRunner(
        registry=registry,
        runner=runner,
        gate=gate,
        approvals=approvals,
    )

    ctx = ToolContext(workspace_root=tmp_path, timeout_s=1.0)
    outcome = await enforced.execute_tool_call(
        _tool_call(tool_name="tests.counting", scope="fs.read", args={"count": "nope"}),
        ctx=ctx,
    )

    assert counter[0] == 0
    assert outcome.decision.action == PolicyAction.DENY
    assert outcome.decision.reason_code == REASON_ARGS_INVALID
    assert outcome.result.ok is False
    assert outcome.result.error_code == POLICY_DENIED_ERROR_CODE


@pytest.mark.asyncio
async def test_policy_deny_does_not_invoke_tool(tmp_path: Path) -> None:
    settings = ReflexorSettings(workspace_root=tmp_path, enabled_scopes=[])
    counter = [0]

    registry = ToolRegistry()
    registry.register(CountingTool(counter))
    runner = ToolRunner(registry=registry, settings=settings)
    gate = PolicyGate(rules=[ScopeEnabledRule()], settings=settings)
    approvals = InMemoryApprovalStore()
    enforced = PolicyEnforcedToolRunner(
        registry=registry,
        runner=runner,
        gate=gate,
        approvals=approvals,
    )

    ctx = ToolContext(workspace_root=tmp_path, timeout_s=1.0)
    outcome = await enforced.execute_tool_call(
        _tool_call(tool_name="tests.counting", scope="fs.read", args={"count": 1}),
        ctx=ctx,
    )

    assert counter[0] == 0
    assert outcome.decision.action == PolicyAction.DENY
    assert outcome.decision.reason_code == REASON_SCOPE_DISABLED
    assert outcome.result.ok is False
    assert outcome.result.error_code == POLICY_DENIED_ERROR_CODE


@pytest.mark.asyncio
async def test_policy_require_approval_creates_pending_and_does_not_invoke_tool(
    tmp_path: Path,
) -> None:
    settings = ReflexorSettings(
        workspace_root=tmp_path,
        enabled_scopes=["fs.read"],
        approval_required_scopes=["fs.read"],
    )
    counter = [0]

    registry = ToolRegistry()
    registry.register(CountingTool(counter))
    runner = ToolRunner(registry=registry, settings=settings)
    gate = PolicyGate(rules=[ScopeEnabledRule(), ApprovalRequiredRule()], settings=settings)
    approvals = InMemoryApprovalStore()
    enforced = PolicyEnforcedToolRunner(
        registry=registry,
        runner=runner,
        gate=gate,
        approvals=approvals,
    )

    ctx = ToolContext(workspace_root=tmp_path, timeout_s=1.0)
    tool_call = _tool_call(tool_name="tests.counting", scope="fs.read", args={"count": 1})
    outcome = await enforced.execute_tool_call(tool_call, ctx=ctx)

    assert counter[0] == 0
    assert outcome.decision.action == PolicyAction.REQUIRE_APPROVAL
    assert outcome.result.ok is False
    assert outcome.result.error_code == APPROVAL_REQUIRED_ERROR_CODE
    assert outcome.approval_id is not None
    assert isinstance(outcome.result.data, dict)
    assert outcome.result.data["approval_id"] == outcome.approval_id

    stored = await approvals.get_by_tool_call(tool_call.tool_call_id)
    assert stored is not None
    assert stored.approval_id == outcome.approval_id
    assert stored.status == ApprovalStatus.PENDING


@pytest.mark.asyncio
async def test_policy_allow_invokes_tool_exactly_once(tmp_path: Path) -> None:
    settings = ReflexorSettings(workspace_root=tmp_path, enabled_scopes=["fs.read"])
    counter = [0]

    registry = ToolRegistry()
    registry.register(CountingTool(counter))
    runner = ToolRunner(registry=registry, settings=settings)
    gate = PolicyGate(rules=[ScopeEnabledRule()], settings=settings)
    approvals = InMemoryApprovalStore()
    enforced = PolicyEnforcedToolRunner(
        registry=registry,
        runner=runner,
        gate=gate,
        approvals=approvals,
    )

    ctx = ToolContext(workspace_root=tmp_path, timeout_s=1.0)
    outcome = await enforced.execute_tool_call(
        _tool_call(tool_name="tests.counting", scope="fs.read", args={"count": 2}),
        ctx=ctx,
    )

    assert counter[0] == 1
    assert outcome.decision.action == PolicyAction.ALLOW
    assert outcome.result.ok is True


@pytest.mark.asyncio
async def test_approval_payload_hash_mismatch_is_denied(tmp_path: Path) -> None:
    settings = ReflexorSettings(
        workspace_root=tmp_path,
        enabled_scopes=["fs.read"],
        approval_required_scopes=["fs.read"],
    )
    counter = [0]

    registry = ToolRegistry()
    registry.register(CountingTool(counter))
    runner = ToolRunner(registry=registry, settings=settings)
    gate = PolicyGate(rules=[ScopeEnabledRule(), ApprovalRequiredRule()], settings=settings)
    approvals = InMemoryApprovalStore()
    builder = ApprovalBuilder(settings=settings)
    enforced = PolicyEnforcedToolRunner(
        registry=registry,
        runner=runner,
        gate=gate,
        approvals=approvals,
        approval_builder=builder,
    )

    tool_call_id = str(uuid4())
    payload_hash, _ = builder.build_payload_hash_for_args(args={"count": 1})
    created = await approvals.create_pending(
        Approval(
            run_id=str(uuid4()),
            task_id=str(uuid4()),
            tool_call_id=tool_call_id,
            payload_hash=payload_hash,
        )
    )

    ctx = ToolContext(workspace_root=tmp_path, timeout_s=1.0)
    outcome = await enforced.execute_tool_call(
        ToolCall(
            tool_call_id=tool_call_id,
            tool_name="tests.counting",
            permission_scope="fs.read",
            idempotency_key="k",
            args={"count": 2},
        ),
        ctx=ctx,
    )

    assert counter[0] == 0
    assert outcome.decision.action == PolicyAction.DENY
    assert outcome.decision.reason_code == REASON_ARGS_INVALID
    assert outcome.result.ok is False
    assert outcome.result.error_code == POLICY_DENIED_ERROR_CODE
    assert outcome.approval_id == created.approval_id
    assert outcome.approval_status == ApprovalStatus.PENDING
    assert isinstance(outcome.result.data, dict)
    assert outcome.result.data["approval_id"] == created.approval_id

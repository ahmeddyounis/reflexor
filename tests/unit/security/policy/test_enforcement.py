from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from reflexor.config import ReflexorSettings
from reflexor.domain.enums import ApprovalStatus
from reflexor.domain.models import ToolCall
from reflexor.security.policy.approvals import InMemoryApprovalStore
from reflexor.security.policy.decision import (
    REASON_SCOPE_DISABLED,
    PolicyAction,
)
from reflexor.security.policy.enforcement import (
    APPROVAL_REQUIRED_ERROR_CODE,
    POLICY_DENIED_ERROR_CODE,
    PolicyEnforcedToolRunner,
)
from reflexor.security.policy.gate import PolicyGate
from reflexor.security.policy.rules import ApprovalRequiredRule, ScopeEnabledRule
from reflexor.tools.mock_tool import MockTool
from reflexor.tools.registry import ToolRegistry
from reflexor.tools.runner import ToolRunner
from reflexor.tools.sdk import ToolContext, ToolResult


def _tool_call(*, tool_call_id: str, scope: str) -> ToolCall:
    return ToolCall(
        tool_call_id=tool_call_id,
        tool_name="tests.recording",
        permission_scope=scope,
        idempotency_key="k",
        args={
            "url": "https://example.com/path?token=sk-super-secret-token-1234567890",
            "headers": {"Authorization": "Bearer abcdefghijklmnopqrstuvwxyz"},
            "body": "hello sk-super-secret-token-1234567890",
        },
    )


def _gate(*, settings: ReflexorSettings) -> PolicyGate:
    return PolicyGate(rules=[ScopeEnabledRule(), ApprovalRequiredRule()], settings=settings)


@pytest.mark.asyncio
async def test_denied_never_invokes_tool(tmp_path: Path) -> None:
    settings = ReflexorSettings(workspace_root=tmp_path, enabled_scopes=[])
    tool = MockTool(tool_name="tests.recording", permission_scope="fs.read")

    registry = ToolRegistry()
    registry.register(tool)

    runner = ToolRunner(registry=registry, settings=settings)
    gate = _gate(settings=settings)
    approvals = InMemoryApprovalStore()
    enforced = PolicyEnforcedToolRunner(
        registry=registry, runner=runner, gate=gate, approvals=approvals
    )

    outcome = await enforced.execute_tool_call(
        _tool_call(tool_call_id=str(uuid4()), scope="fs.read"),
        ctx=ToolContext(workspace_root=tmp_path, timeout_s=1.0),
    )

    assert tool.invocations == []
    assert outcome.decision.action == PolicyAction.DENY
    assert outcome.decision.reason_code == REASON_SCOPE_DISABLED
    assert outcome.result.ok is False
    assert outcome.result.error_code == POLICY_DENIED_ERROR_CODE


@pytest.mark.asyncio
async def test_approval_required_creates_single_pending_and_never_invokes_tool(
    tmp_path: Path,
) -> None:
    settings = ReflexorSettings(
        workspace_root=tmp_path,
        enabled_scopes=["fs.read"],
        approval_required_scopes=["fs.read"],
    )
    tool = MockTool(tool_name="tests.recording", permission_scope="fs.read")

    registry = ToolRegistry()
    registry.register(tool)

    runner = ToolRunner(registry=registry, settings=settings)
    gate = _gate(settings=settings)
    approvals = InMemoryApprovalStore()
    enforced = PolicyEnforcedToolRunner(
        registry=registry, runner=runner, gate=gate, approvals=approvals
    )

    tool_call_id = str(uuid4())
    tool_call = _tool_call(tool_call_id=tool_call_id, scope="fs.read")
    ctx = ToolContext(workspace_root=tmp_path, timeout_s=1.0)

    first = await enforced.execute_tool_call(tool_call, ctx=ctx)
    second = await enforced.execute_tool_call(tool_call, ctx=ctx)

    assert tool.invocations == []
    assert first.result.ok is False
    assert first.result.error_code == APPROVAL_REQUIRED_ERROR_CODE
    assert first.approval_id is not None
    assert second.approval_id == first.approval_id

    stored = await approvals.get_by_tool_call(tool_call_id)
    assert stored is not None
    assert stored.status == ApprovalStatus.PENDING
    assert stored.preview is not None

    secret = "sk-super-secret-token-1234567890"
    assert secret not in stored.preview
    assert secret not in json.dumps(first.model_dump(mode="json"), separators=(",", ":"))


@pytest.mark.asyncio
async def test_approval_decision_controls_execution(tmp_path: Path) -> None:
    settings = ReflexorSettings(
        workspace_root=tmp_path,
        enabled_scopes=["fs.read"],
        approval_required_scopes=["fs.read"],
    )
    tool = MockTool(tool_name="tests.recording", permission_scope="fs.read")

    registry = ToolRegistry()
    registry.register(tool)

    runner = ToolRunner(registry=registry, settings=settings)
    gate = _gate(settings=settings)
    approvals = InMemoryApprovalStore()
    enforced = PolicyEnforcedToolRunner(
        registry=registry, runner=runner, gate=gate, approvals=approvals
    )

    tool_call_id = str(uuid4())
    tool_call = _tool_call(tool_call_id=tool_call_id, scope="fs.read")
    ctx = ToolContext(workspace_root=tmp_path, timeout_s=1.0)

    pending = await enforced.execute_tool_call(tool_call, ctx=ctx)
    assert pending.approval_id is not None
    assert tool.invocations == []

    decided = await approvals.decide(
        pending.approval_id, ApprovalStatus.APPROVED, decided_by="tester"
    )
    assert decided.status == ApprovalStatus.APPROVED

    allowed = await enforced.execute_tool_call(tool_call, ctx=ctx)
    assert allowed.result.ok is True
    assert len(tool.invocations) == 1

    tool.reset()
    tool_call_2 = _tool_call(tool_call_id=str(uuid4()), scope="fs.read")
    pending_2 = await enforced.execute_tool_call(tool_call_2, ctx=ctx)
    assert pending_2.approval_id is not None

    denied = await approvals.decide(
        pending_2.approval_id, ApprovalStatus.DENIED, decided_by="tester2"
    )
    assert denied.status == ApprovalStatus.DENIED

    blocked = await enforced.execute_tool_call(tool_call_2, ctx=ctx)
    assert blocked.result.ok is False
    assert blocked.result.error_code == POLICY_DENIED_ERROR_CODE
    assert tool.invocations == []


@pytest.mark.asyncio
async def test_allow_path_runs_tool_and_sanitizes_result(tmp_path: Path) -> None:
    settings = ReflexorSettings(workspace_root=tmp_path, enabled_scopes=["fs.read"])
    tool = MockTool(tool_name="tests.recording", permission_scope="fs.read")

    registry = ToolRegistry()
    registry.register(tool)

    runner = ToolRunner(registry=registry, settings=settings)
    gate = _gate(settings=settings)
    approvals = InMemoryApprovalStore()
    enforced = PolicyEnforcedToolRunner(
        registry=registry, runner=runner, gate=gate, approvals=approvals
    )

    tool_call = _tool_call(tool_call_id=str(uuid4()), scope="fs.read")
    tool.set_static_result(
        tool_call.args,
        ToolResult(
            ok=True,
            data={
                "token": "sk-super-secret-token-1234567890",
                "authorization": "Bearer abcdefghijklmnopqrstuvwxyz",
                "text": "Bearer abcdefghijklmnopqrstuvwxyz",
            },
        ),
    )
    outcome = await enforced.execute_tool_call(
        tool_call,
        ctx=ToolContext(workspace_root=tmp_path, timeout_s=1.0),
    )

    assert outcome.decision.action == PolicyAction.ALLOW
    assert outcome.result.ok is True
    assert len(tool.invocations) == 1
    assert isinstance(outcome.result.data, dict)

    data = outcome.result.data
    assert data["token"] == "<redacted>"
    assert data["authorization"] == "<redacted>"
    assert "abcdefghijklmnopqrstuvwxyz" not in data["text"]
    assert "<redacted>" in data["text"]

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel

from reflexor.config import ReflexorSettings
from reflexor.domain.models import ToolCall
from reflexor.guards import GuardChain, GuardDecision
from reflexor.security.policy.approvals import InMemoryApprovalStore
from reflexor.security.policy.enforcement import POLICY_DENIED_ERROR_CODE, PolicyEnforcedToolRunner
from reflexor.security.policy.gate import PolicyGate
from reflexor.security.policy.rules import ScopeEnabledRule
from reflexor.tools.registry import ToolRegistry
from reflexor.tools.runner import ToolRunner
from reflexor.tools.sdk import ToolContext, ToolManifest, ToolResult


class _Args(BaseModel):
    n: int


class _CountingTool:
    manifest = ToolManifest(
        name="tests.guard_counting",
        version="0.1.0",
        description="Tool for guard pipeline tests.",
        permission_scope="fs.read",
        idempotent=True,
    )
    ArgsModel = _Args

    def __init__(self, counter: list[int]) -> None:
        self._counter = counter

    async def run(self, args: _Args, ctx: ToolContext) -> ToolResult:
        _ = args
        _ = ctx
        self._counter[0] += 1
        return ToolResult(ok=True, data={"ok": True})


class _DenyGuard:
    async def check(self, *_: object, **__: object) -> GuardDecision:
        return GuardDecision.deny(
            reason_code="test_guard_denied",
            message="blocked by test guard",
            guard_id="tests.deny_guard",
        )


@pytest.mark.asyncio
async def test_policy_enforced_tool_runner_invokes_guard_chain(tmp_path: Path) -> None:
    settings = ReflexorSettings(workspace_root=tmp_path, enabled_scopes=["fs.read"])
    counter = [0]

    registry = ToolRegistry()
    registry.register(_CountingTool(counter))

    runner = ToolRunner(registry=registry, settings=settings)
    gate = PolicyGate(rules=[ScopeEnabledRule()], settings=settings)
    approvals = InMemoryApprovalStore()

    enforced = PolicyEnforcedToolRunner(
        registry=registry,
        runner=runner,
        gate=gate,
        approvals=approvals,
        guard_chain=GuardChain([_DenyGuard()]),
    )

    ctx = ToolContext(workspace_root=tmp_path, timeout_s=1.0)
    outcome = await enforced.execute_tool_call(
        ToolCall(
            tool_name="tests.guard_counting",
            permission_scope="fs.read",
            idempotency_key="k",
            args={"n": 1},
        ),
        ctx=ctx,
    )

    assert counter[0] == 0
    assert outcome.result.ok is False
    assert outcome.result.error_code == POLICY_DENIED_ERROR_CODE
    assert outcome.decision.action.value == "deny"
    assert outcome.decision.reason_code == "test_guard_denied"

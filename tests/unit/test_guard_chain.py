from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel

from reflexor.config import ReflexorSettings
from reflexor.domain.models import ToolCall
from reflexor.guards import GuardAction, GuardChain, GuardContext, GuardDecision
from reflexor.security.policy.context import PolicyContext, ToolSpec
from reflexor.tools.sdk import ToolManifest


class _Args(BaseModel):
    n: int = 1


class _StaticGuard:
    def __init__(self, decision: GuardDecision) -> None:
        self._decision = decision

    async def check(self, *_: object, **__: object) -> GuardDecision:
        return self._decision


class _ExplodingGuard:
    async def check(self, *_: object, **__: object) -> GuardDecision:  # pragma: no cover
        raise AssertionError("guard chain should have short-circuited before this guard")


def _ctx(tmp_path: Path) -> GuardContext:
    settings = ReflexorSettings(workspace_root=tmp_path)
    return GuardContext(policy=PolicyContext.from_settings(settings), now_ms=0)


def _inputs(tmp_path: Path) -> tuple[ToolCall, ToolSpec, _Args]:
    tool_call = ToolCall(
        tool_name="tests.guard_chain",
        permission_scope="fs.read",
        idempotency_key="k",
        args={"n": 1},
    )
    tool_spec = ToolSpec(
        tool_name="tests.guard_chain",
        manifest=ToolManifest(
            name="tests.guard_chain",
            version="0.1.0",
            description="guard chain test tool",
            permission_scope="fs.read",
        ),
        args_model=_Args,
    )
    return tool_call, tool_spec, _Args.model_validate(tool_call.args)


@pytest.mark.asyncio
async def test_guard_chain_defaults_to_allow_when_empty(tmp_path: Path) -> None:
    tool_call, tool_spec, parsed = _inputs(tmp_path)
    decision = await GuardChain([]).check(tool_call, tool_spec, parsed, _ctx(tmp_path))
    assert decision.action == GuardAction.ALLOW


@pytest.mark.asyncio
async def test_guard_chain_delay_beats_allow(tmp_path: Path) -> None:
    tool_call, tool_spec, parsed = _inputs(tmp_path)
    chain = GuardChain(
        [
            _StaticGuard(GuardDecision.allow(reason_code="guard1_ok")),
            _StaticGuard(GuardDecision.delay(delay_s=1.0, reason_code="rate_limited")),
        ]
    )
    decision = await chain.check(tool_call, tool_spec, parsed, _ctx(tmp_path))
    assert decision.action == GuardAction.DELAY
    assert decision.reason_code == "rate_limited"


@pytest.mark.asyncio
async def test_guard_chain_require_approval_beats_delay(tmp_path: Path) -> None:
    tool_call, tool_spec, parsed = _inputs(tmp_path)
    chain = GuardChain(
        [
            _StaticGuard(GuardDecision.delay(delay_s=1.0, reason_code="rate_limited")),
            _StaticGuard(GuardDecision.require_approval(reason_code="needs_human")),
        ]
    )
    decision = await chain.check(tool_call, tool_spec, parsed, _ctx(tmp_path))
    assert decision.action == GuardAction.REQUIRE_APPROVAL
    assert decision.reason_code == "needs_human"


@pytest.mark.asyncio
async def test_guard_chain_deny_beats_require_approval_and_short_circuits(tmp_path: Path) -> None:
    tool_call, tool_spec, parsed = _inputs(tmp_path)
    chain = GuardChain(
        [
            _StaticGuard(GuardDecision.require_approval(reason_code="needs_human")),
            _StaticGuard(GuardDecision.deny(reason_code="circuit_open")),
            _ExplodingGuard(),
        ]
    )
    decision = await chain.check(tool_call, tool_spec, parsed, _ctx(tmp_path))
    assert decision.action == GuardAction.DENY
    assert decision.reason_code == "circuit_open"


@pytest.mark.asyncio
async def test_guard_chain_is_deterministic_for_ties(tmp_path: Path) -> None:
    tool_call, tool_spec, parsed = _inputs(tmp_path)
    chain = GuardChain(
        [
            _StaticGuard(GuardDecision.delay(delay_s=1.0, reason_code="delay_first")),
            _StaticGuard(GuardDecision.delay(delay_s=2.0, reason_code="delay_second")),
        ]
    )
    decision = await chain.check(tool_call, tool_spec, parsed, _ctx(tmp_path))
    assert decision.action == GuardAction.DELAY
    assert decision.reason_code == "delay_first"

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel

from reflexor.config import ReflexorSettings
from reflexor.domain.models import ToolCall
from reflexor.guards import GuardAction, GuardContext
from reflexor.guards.circuit_breaker import (
    CircuitBreakerDecision,
    CircuitBreakerGuard,
    CircuitBreakerKey,
    CircuitState,
)
from reflexor.security.policy.context import PolicyContext, ToolSpec
from reflexor.tools.sdk import ToolManifest


class _Args(BaseModel):
    target_url: str


class _RecordingBreaker:
    def __init__(self, *, decision: CircuitBreakerDecision) -> None:
        self._decision = decision
        self.calls: list[tuple[CircuitBreakerKey, float]] = []

    async def allow_call(
        self, *, key: CircuitBreakerKey, now_s: float
    ) -> CircuitBreakerDecision:
        self.calls.append((key, now_s))
        return self._decision

    async def record_result(
        self, *, key: CircuitBreakerKey, ok: bool, now_s: float
    ) -> None:  # pragma: no cover
        _ = (key, ok, now_s)
        raise AssertionError("record_result should not be called in this test")


class _ExplodingBreaker:
    async def allow_call(
        self, *, key: CircuitBreakerKey, now_s: float
    ) -> CircuitBreakerDecision:  # pragma: no cover
        _ = (key, now_s)
        raise AssertionError("circuit breaker should not be called")

    async def record_result(
        self, *, key: CircuitBreakerKey, ok: bool, now_s: float
    ) -> None:  # pragma: no cover
        _ = (key, ok, now_s)
        raise AssertionError("record_result should not be called in this test")


def _tool_call(url: str) -> ToolCall:
    return ToolCall(
        tool_name="tests.circuit_breaker_guard",
        permission_scope="net.http",
        idempotency_key="k",
        args={"target_url": url},
    )


def _tool_spec() -> ToolSpec:
    manifest = ToolManifest(
        name="tests.circuit_breaker_guard",
        version="0.1.0",
        description="circuit breaker guard test tool",
        permission_scope="net.http",
        side_effects=True,
    )
    return ToolSpec(tool_name=manifest.name, manifest=manifest, args_model=_Args)


def _ctx(settings: ReflexorSettings) -> GuardContext:
    return GuardContext(policy=PolicyContext.from_settings(settings), now_ms=5_000)


def test_circuit_breaker_guard_rejects_invalid_half_open_throttle_delay() -> None:
    with pytest.raises(ValueError, match="half_open_throttle_delay_s must be finite and >= 0"):
        CircuitBreakerGuard(
            breaker=_RecordingBreaker(
                decision=CircuitBreakerDecision(allowed=True, state=CircuitState.CLOSED)
            ),
            half_open_throttle_delay_s=float("nan"),
        )

    with pytest.raises(ValueError, match="half_open_throttle_delay_s must be finite and >= 0"):
        CircuitBreakerGuard(
            breaker=_RecordingBreaker(
                decision=CircuitBreakerDecision(allowed=True, state=CircuitState.CLOSED)
            ),
            half_open_throttle_delay_s=-0.1,
        )


@pytest.mark.asyncio
async def test_circuit_breaker_guard_uses_target_url_destination(tmp_path: Path) -> None:
    breaker = _RecordingBreaker(
        decision=CircuitBreakerDecision(allowed=True, state=CircuitState.CLOSED)
    )
    guard = CircuitBreakerGuard(breaker=breaker)
    tool_call = _tool_call("https://bücher.example/path")
    decision = await guard.check(
        tool_call=tool_call,
        tool_spec=_tool_spec(),
        parsed_args=_Args(target_url="https://bücher.example/path"),
        ctx=_ctx(ReflexorSettings(workspace_root=tmp_path)),
    )

    assert decision.action == GuardAction.ALLOW
    assert len(breaker.calls) == 1
    key, now_s = breaker.calls[0]
    assert key.destination == "xn--bcher-kva.example"
    assert now_s == 5.0


@pytest.mark.asyncio
async def test_circuit_breaker_guard_skips_breaker_when_domain_still_needs_approval(
    tmp_path: Path,
) -> None:
    guard = CircuitBreakerGuard(breaker=_ExplodingBreaker())
    tool_call = _tool_call("https://sensitive.example/path")
    settings = ReflexorSettings(
        workspace_root=tmp_path,
        approval_required_domains=["sensitive.example"],
    )

    decision = await guard.check(
        tool_call=tool_call,
        tool_spec=_tool_spec(),
        parsed_args=_Args(target_url="https://sensitive.example/path"),
        ctx=_ctx(settings),
    )

    assert decision.action == GuardAction.ALLOW

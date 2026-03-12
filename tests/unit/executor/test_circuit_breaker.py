from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import pytest

from reflexor.domain.models import ToolCall
from reflexor.executor.service.circuit_breaker import record_circuit_breaker_result
from reflexor.guards.circuit_breaker import CircuitBreakerKey
from reflexor.security.policy.decision import PolicyDecision
from reflexor.security.policy.enforcement import ToolExecutionOutcome
from reflexor.tools.sdk import ToolResult


@dataclass(slots=True)
class _Clock:
    now: int = 5_000

    def now_ms(self) -> int:
        return self.now


class _RecordingBreaker:
    def __init__(self) -> None:
        self.calls: list[tuple[CircuitBreakerKey, bool, float]] = []

    async def record_result(self, *, key: CircuitBreakerKey, ok: bool, now_s: float) -> None:
        self.calls.append((key, ok, now_s))


@dataclass(slots=True)
class _ServiceStub:
    _circuit_breaker: _RecordingBreaker
    _clock: _Clock


@pytest.mark.asyncio
async def test_record_circuit_breaker_result_uses_url_like_args() -> None:
    breaker = _RecordingBreaker()
    service = cast(Any, _ServiceStub(_circuit_breaker=breaker, _clock=_Clock()))
    tool_call = ToolCall(
        tool_name=" Tests.Webhook ",
        permission_scope="net.http",
        idempotency_key="k",
        args={"target_url": "https://bücher.example/hooks"},
    )
    outcome = ToolExecutionOutcome(
        tool_call_id=tool_call.tool_call_id,
        tool_name=tool_call.tool_name,
        decision=PolicyDecision.allow(),
        result=ToolResult(ok=False, error_code="TIMEOUT", error_message="timed out"),
    )

    await record_circuit_breaker_result(service, tool_call=tool_call, outcome=outcome)

    assert breaker.calls == [
        (
            CircuitBreakerKey(tool_name="tests.webhook", destination="xn--bcher-kva.example"),
            False,
            5.0,
        )
    ]

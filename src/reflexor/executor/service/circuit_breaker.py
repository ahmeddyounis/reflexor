from __future__ import annotations

from typing import TYPE_CHECKING

from reflexor.domain.models import ToolCall
from reflexor.executor.service.outcomes import did_attempt_tool_run
from reflexor.guards.circuit_breaker.resolver import key_for_tool_call
from reflexor.security.policy.enforcement import (
    EXECUTION_DELAYED_ERROR_CODE,
    ToolExecutionOutcome,
)

if TYPE_CHECKING:
    from reflexor.executor.service.core import ExecutorService


async def record_circuit_breaker_result(
    service: ExecutorService,
    *,
    tool_call: ToolCall,
    outcome: ToolExecutionOutcome,
) -> None:
    if service._circuit_breaker is None:
        return
    if outcome.result.error_code == EXECUTION_DELAYED_ERROR_CODE:
        return
    if not did_attempt_tool_run(outcome):
        return

    key = key_for_tool_call(tool_name=tool_call.tool_name, args=tool_call.args)
    now_s = float(service._clock.now_ms()) / 1000.0
    try:
        await service._circuit_breaker.record_result(
            key=key,
            ok=bool(outcome.result.ok),
            now_s=now_s,
        )
    except Exception:
        # Best-effort: never fail the task because the circuit breaker store is down.
        return

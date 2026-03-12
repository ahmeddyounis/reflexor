from __future__ import annotations

import math

from pydantic import BaseModel

from reflexor.domain.enums import ApprovalStatus
from reflexor.domain.models import ToolCall
from reflexor.guards.circuit_breaker.interface import CircuitBreaker
from reflexor.guards.circuit_breaker.resolver import key_for_tool_call
from reflexor.guards.context import GuardContext
from reflexor.guards.decision import GuardDecision
from reflexor.observability.metrics import ReflexorMetrics
from reflexor.security.policy.context import ToolSpec
from reflexor.security.policy.rules import ApprovalRequiredRule

REASON_CIRCUIT_OPEN = "circuit_open"
REASON_CIRCUIT_HALF_OPEN = "circuit_half_open"


class CircuitBreakerGuard:
    """Delay tool calls when the breaker is OPEN or HALF_OPEN-saturated."""

    def __init__(
        self,
        *,
        breaker: CircuitBreaker,
        metrics: ReflexorMetrics | None = None,
        half_open_throttle_delay_s: float = 0.1,
    ) -> None:
        self._breaker = breaker
        self._metrics = metrics
        self._approval_rule = ApprovalRequiredRule()
        delay_s = float(half_open_throttle_delay_s)
        if not math.isfinite(delay_s) or delay_s < 0:
            raise ValueError("half_open_throttle_delay_s must be finite and >= 0")
        self._half_open_throttle_delay_s = delay_s

    async def check(
        self,
        tool_call: ToolCall,
        tool_spec: ToolSpec,
        parsed_args: BaseModel,
        ctx: GuardContext,
    ) -> GuardDecision:
        # Avoid acquiring HALF_OPEN permits when we know execution cannot proceed
        # because policy still requires approval for this tool call.
        if (
            ctx.approval_status != ApprovalStatus.APPROVED
            and (
                ctx.policy.approval_required_scopes
                or ctx.policy.approval_required_domains
                or ctx.policy.approval_required_payload_keywords
                or (
                    ctx.policy.profile == "prod"
                    and tool_spec.manifest.side_effects
                    and not ctx.policy.dry_run
                )
            )
        ):
            approval_required = self._approval_rule.evaluate(
                tool_call=tool_call,
                tool_spec=tool_spec,
                parsed_args=parsed_args,
                ctx=ctx.policy,
            )
            if approval_required is not None:
                return GuardDecision.allow()

        key = key_for_tool_call(
            tool_name=tool_call.tool_name,
            args=parsed_args,
        )

        now_s = float(ctx.now_ms) / 1000.0
        decision = await self._breaker.allow_call(key=key, now_s=now_s)

        if ctx.emit_metrics and self._metrics is not None:
            self._metrics.circuit_breaker_checks_total.labels(
                state=decision.state.value,
                allowed="true" if decision.allowed else "false",
            ).inc()

        if decision.allowed:
            return GuardDecision.allow(
                guard_id="guard.circuit_breaker",
                metadata={
                    "circuit_state": decision.state.value,
                    "circuit_key": {
                        "tool_name": key.tool_name,
                        "destination": key.destination,
                    },
                },
            )

        retry_after_s = decision.retry_after_s
        delay_s = 0.0 if retry_after_s is None else max(0.0, float(retry_after_s))

        if decision.state.value == "half_open" and delay_s <= 0:
            delay_s = float(self._half_open_throttle_delay_s)

        reason_code = (
            REASON_CIRCUIT_OPEN if decision.state.value == "open" else REASON_CIRCUIT_HALF_OPEN
        )
        return GuardDecision.delay(
            delay_s=delay_s,
            reason_code=reason_code,
            message="circuit breaker blocked execution",
            guard_id="guard.circuit_breaker",
            metadata={
                "circuit_state": decision.state.value,
                "circuit_retry_after_s": decision.retry_after_s,
                "circuit_key": {
                    "tool_name": key.tool_name,
                    "destination": key.destination,
                },
            },
        )


__all__ = [
    "CircuitBreakerGuard",
    "REASON_CIRCUIT_HALF_OPEN",
    "REASON_CIRCUIT_OPEN",
]

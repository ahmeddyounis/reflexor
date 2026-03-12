from __future__ import annotations

import math

from pydantic import BaseModel

from reflexor.domain.models import ToolCall
from reflexor.guards.context import GuardContext
from reflexor.guards.decision import GuardDecision
from reflexor.guards.rate_limit.policy import RateLimitPolicy
from reflexor.security.policy.context import ToolSpec

REASON_RATE_LIMITED = "rate_limited"
REASON_RATE_LIMIT_UNSATISFIABLE = "rate_limit_unsatisfiable"


class RateLimitGuard:
    """Execution guard that delays tool calls when over configured rate limits."""

    def __init__(
        self,
        *,
        policy: RateLimitPolicy,
        cost: float = 1.0,
    ) -> None:
        self._policy = policy
        cost_f = float(cost)
        if not math.isfinite(cost_f) or cost_f < 0:
            raise ValueError("cost must be finite and >= 0")
        self._cost = cost_f

    async def check(
        self,
        tool_call: ToolCall,
        tool_spec: ToolSpec,
        parsed_args: BaseModel,
        ctx: GuardContext,
    ) -> GuardDecision:
        _ = tool_spec
        if not self._policy.settings.rate_limits_enabled:
            return GuardDecision.allow()

        now_s = float(ctx.now_ms) / 1000.0
        result = await self._policy.consume(
            tool_call=tool_call,
            parsed_args=parsed_args,
            run_id=ctx.run_id,
            cost=self._cost,
            now_s=now_s,
        )
        if result.allowed:
            return GuardDecision.allow()

        if result.retry_after_s is None:
            return GuardDecision.deny(
                reason_code=REASON_RATE_LIMIT_UNSATISFIABLE,
                message="rate limit would never allow this request (check capacity/refill/cost)",
                guard_id="guard.rate_limit",
            )

        delay_s = max(0.0, float(result.retry_after_s))
        return GuardDecision.delay(
            delay_s=delay_s,
            reason_code=REASON_RATE_LIMITED,
            message="rate limited",
            guard_id="guard.rate_limit",
        )


__all__ = [
    "REASON_RATE_LIMITED",
    "REASON_RATE_LIMIT_UNSATISFIABLE",
    "RateLimitGuard",
]

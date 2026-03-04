from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel

from reflexor.domain.models import ToolCall
from reflexor.guards.context import GuardContext
from reflexor.guards.decision import GuardAction, GuardDecision
from reflexor.guards.interface import ExecutionGuard
from reflexor.security.policy.context import ToolSpec

_ACTION_RANK: dict[GuardAction, int] = {
    GuardAction.ALLOW: 0,
    GuardAction.DELAY: 1,
    GuardAction.REQUIRE_APPROVAL: 2,
    GuardAction.DENY: 3,
}


class GuardChain:
    """Deterministic guard evaluation with action precedence."""

    def __init__(self, guards: Sequence[ExecutionGuard] | None = None) -> None:
        self._guards = list(guards or [])

    @property
    def guards(self) -> tuple[ExecutionGuard, ...]:
        return tuple(self._guards)

    async def check(
        self,
        tool_call: ToolCall,
        tool_spec: ToolSpec,
        parsed_args: BaseModel,
        ctx: GuardContext,
    ) -> GuardDecision:
        best = GuardDecision.allow()
        best_rank = _ACTION_RANK[best.action]

        for guard in self._guards:
            decision = await guard.check(tool_call, tool_spec, parsed_args, ctx)
            rank = _ACTION_RANK[decision.action]

            if decision.action == GuardAction.DENY:
                return decision

            if rank > best_rank:
                best = decision
                best_rank = rank

        return best


__all__ = ["GuardChain"]

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

from reflexor.domain.models import ToolCall
from reflexor.guards.context import GuardContext
from reflexor.guards.decision import GuardDecision
from reflexor.security.policy.context import ToolSpec


class ExecutionGuard(Protocol):
    """A pre-execution check that can allow/deny/delay/require approval."""

    def check(
        self,
        tool_call: ToolCall,
        tool_spec: ToolSpec,
        parsed_args: BaseModel,
        ctx: GuardContext,
    ) -> GuardDecision: ...


__all__ = ["ExecutionGuard"]

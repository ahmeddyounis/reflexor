"""Policy gate (evaluation entrypoint).

The policy gate is the primary entrypoint used by executors to evaluate a tool manifest against
configured policy rules.

Clean Architecture:
This module may depend on `reflexor.config`, `reflexor.domain`, and `reflexor.security.*`
utilities. It must not import infrastructure/framework layers.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel

from reflexor.config import ReflexorSettings, get_settings
from reflexor.domain.models import ToolCall
from reflexor.security.policy.context import PolicyContext, ToolSpec
from reflexor.security.policy.decision import PolicyDecision
from reflexor.security.policy.rules import PolicyRule, evaluate_rules


class PolicyGate:
    """Evaluate tool calls with a configured set of policy rules."""

    def __init__(
        self,
        *,
        rules: Sequence[PolicyRule] | None = None,
        settings: ReflexorSettings | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._rules = list(rules or [])

    @property
    def settings(self) -> ReflexorSettings:
        return self._settings

    def evaluate(
        self,
        *,
        tool_call: ToolCall,
        tool_spec: ToolSpec,
        parsed_args: BaseModel,
        ctx: PolicyContext | None = None,
    ) -> PolicyDecision:
        resolved_ctx = ctx or PolicyContext.from_settings(self._settings)
        return evaluate_rules(
            self._rules,
            tool_call=tool_call,
            tool_spec=tool_spec,
            parsed_args=parsed_args,
            ctx=resolved_ctx,
        )

"""Policy gate (evaluation entrypoint).

The policy gate is the primary entrypoint used by executors to evaluate a tool manifest against
configured policy rules.

Clean Architecture:
This module may depend on `reflexor.config`, `reflexor.domain`, and `reflexor.security.*`
utilities. It must not import infrastructure/framework layers.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, overload

from pydantic import BaseModel, ConfigDict, Field

from reflexor.config import ReflexorSettings, get_settings
from reflexor.domain.models import ToolCall
from reflexor.observability.metrics import ReflexorMetrics
from reflexor.security.policy.context import PolicyContext, ToolSpec
from reflexor.security.policy.decision import REASON_OK, PolicyAction, PolicyDecision
from reflexor.security.policy.rules import PolicyRule


class PolicyTraceEntry(BaseModel):
    """A single rule evaluation result (for debugging/audit traces)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_id: str
    decision: PolicyDecision | None = None


class PolicyEvaluation(BaseModel):
    """Policy evaluation output with an optional trace of rule decisions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: PolicyDecision
    trace: list[PolicyTraceEntry] = Field(default_factory=list)


class PolicyGate:
    """Evaluate tool calls with a configured set of policy rules."""

    def __init__(
        self,
        *,
        rules: Sequence[PolicyRule] | None = None,
        settings: ReflexorSettings | None = None,
        metrics: ReflexorMetrics | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        if rules is None:
            from reflexor.security.policy.defaults import build_default_policy_rules

            resolved_rules = build_default_policy_rules()
        else:
            resolved_rules = list(rules)
        self._rules = resolved_rules
        self._metrics = metrics

    @property
    def settings(self) -> ReflexorSettings:
        return self._settings

    @property
    def metrics(self) -> ReflexorMetrics | None:
        return self._metrics

    def _emit_decision_metric(self, decision: PolicyDecision) -> None:
        if self._metrics is None:
            return
        self._metrics.policy_decisions_total.labels(
            action=decision.action.value,
            reason_code=decision.reason_code,
        ).inc()

    def _evaluate_with_trace(
        self,
        *,
        tool_call: ToolCall,
        tool_spec: ToolSpec,
        parsed_args: BaseModel,
        ctx: PolicyContext,
    ) -> tuple[PolicyDecision, list[PolicyTraceEntry]]:
        trace: list[PolicyTraceEntry] = []
        first_approval: PolicyDecision | None = None

        for rule in self._rules:
            rule_id = getattr(rule, "rule_id", type(rule).__name__)
            decision = rule.evaluate(
                tool_call=tool_call,
                tool_spec=tool_spec,
                parsed_args=parsed_args,
                ctx=ctx,
            )
            trace.append(PolicyTraceEntry(rule_id=rule_id, decision=decision))

            if decision is None:
                continue

            if decision.action == PolicyAction.DENY:
                return decision, trace

            if decision.action == PolicyAction.REQUIRE_APPROVAL and first_approval is None:
                first_approval = decision

        if first_approval is not None:
            return first_approval, trace

        return PolicyDecision.allow(reason_code=REASON_OK), trace

    @overload
    def evaluate(
        self,
        *,
        tool_call: ToolCall,
        tool_spec: ToolSpec,
        parsed_args: BaseModel,
        ctx: PolicyContext | None = None,
        emit_metrics: bool = True,
        policy_trace: Literal[False] = False,
    ) -> PolicyDecision: ...

    @overload
    def evaluate(
        self,
        *,
        tool_call: ToolCall,
        tool_spec: ToolSpec,
        parsed_args: BaseModel,
        ctx: PolicyContext | None = None,
        emit_metrics: bool = True,
        policy_trace: Literal[True],
    ) -> PolicyEvaluation: ...

    def evaluate(
        self,
        *,
        tool_call: ToolCall,
        tool_spec: ToolSpec,
        parsed_args: BaseModel,
        ctx: PolicyContext | None = None,
        emit_metrics: bool = True,
        policy_trace: bool = False,
    ) -> PolicyDecision | PolicyEvaluation:
        resolved_ctx = ctx or PolicyContext.from_settings(self._settings)
        decision, trace = self._evaluate_with_trace(
            tool_call=tool_call,
            tool_spec=tool_spec,
            parsed_args=parsed_args,
            ctx=resolved_ctx,
        )
        if emit_metrics:
            self._emit_decision_metric(decision)
        if not policy_trace:
            return decision
        return PolicyEvaluation(decision=decision, trace=trace)

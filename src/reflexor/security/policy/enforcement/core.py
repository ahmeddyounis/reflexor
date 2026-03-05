"""Decision enforcement helpers (policy boundary for tool execution).

This module provides a non-bypassable boundary for tool execution:
- validates tool args
- evaluates policy
- enforces deny/approval-required outcomes
- delegates allowed tool execution to the ToolRunner

Clean Architecture:
The policy layer must not import infrastructure/framework layers.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from pydantic import BaseModel, ValidationError

from reflexor.domain.enums import ApprovalStatus
from reflexor.domain.models import ToolCall
from reflexor.guards import GuardAction, GuardChain, GuardDecision
from reflexor.guards.defaults import build_default_policy_guard_chain
from reflexor.observability.metrics import ReflexorMetrics
from reflexor.security.policy.approvals import ApprovalBuilder, ApprovalStore
from reflexor.security.policy.context import PolicyContext, ToolSpec, tool_spec_from_tool
from reflexor.security.policy.decision import (
    REASON_ARGS_INVALID,
    REASON_TOOL_UNKNOWN,
    PolicyDecision,
)
from reflexor.security.policy.enforcement.approval_flow import handle_require_approval
from reflexor.security.policy.enforcement.guards import evaluate_guards as _evaluate_guards
from reflexor.security.policy.enforcement.guards import policy_decision_from_guard
from reflexor.security.policy.enforcement.telemetry import (
    emit_decision_metric,
    emit_guard_metrics,
    log_decision,
)
from reflexor.security.policy.enforcement.types import (
    EXECUTION_DELAYED_ERROR_CODE,
    POLICY_DENIED_ERROR_CODE,
    ToolExecutionOutcome,
)
from reflexor.security.policy.gate import PolicyGate
from reflexor.tools.registry import ToolRegistry
from reflexor.tools.runner import ToolRunner
from reflexor.tools.sdk import ToolContext, ToolResult


class PolicyEnforcedToolRunner:
    """Execute tool calls through policy enforcement (cannot be bypassed accidentally)."""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        runner: ToolRunner,
        gate: PolicyGate,
        approvals: ApprovalStore,
        approval_builder: ApprovalBuilder | None = None,
        metrics: ReflexorMetrics | None = None,
        guard_chain: GuardChain | None = None,
        now_ms: Callable[[], int] | None = None,
    ) -> None:
        self._registry = registry
        self._runner = runner
        self._gate = gate
        self._approvals = approvals
        self._approval_builder = approval_builder or ApprovalBuilder(settings=gate.settings)
        self._metrics = metrics
        self._policy_ctx = PolicyContext.from_settings(gate.settings)
        if guard_chain is None:
            guard_chain = build_default_policy_guard_chain(gate=gate)
        self._guard_chain = guard_chain
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))

    @property
    def registry(self) -> ToolRegistry:
        return self._registry

    @property
    def runner(self) -> ToolRunner:
        return self._runner

    @property
    def gate(self) -> PolicyGate:
        return self._gate

    @property
    def approvals(self) -> ApprovalStore:
        return self._approvals

    @property
    def approval_builder(self) -> ApprovalBuilder:
        return self._approval_builder

    @property
    def metrics(self) -> ReflexorMetrics | None:
        return self._metrics

    @property
    def guard_chain(self) -> GuardChain:
        return self._guard_chain

    async def evaluate_guards(
        self,
        *,
        tool_call: ToolCall,
        tool_spec: ToolSpec,
        parsed_args: BaseModel,
        approval_status: ApprovalStatus | None = None,
        emit_metrics: bool = True,
        now_ms: int | None = None,
    ) -> GuardDecision:
        decision = await _evaluate_guards(
            guard_chain=self._guard_chain,
            policy_ctx=self._policy_ctx,
            tool_call=tool_call,
            tool_spec=tool_spec,
            parsed_args=parsed_args,
            approval_status=approval_status,
            emit_metrics=bool(emit_metrics),
            now_ms=now_ms,
            now_ms_func=self._now_ms,
        )
        emit_guard_metrics(
            metrics=self._metrics,
            tool_call=tool_call,
            decision=decision,
            emit_metrics=bool(emit_metrics),
        )
        return decision

    async def execute_tool_call(
        self,
        tool_call: ToolCall,
        *,
        ctx: ToolContext,
        decided_by: str | None = None,
        on_before_execute: Callable[[], Awaitable[None]] | None = None,
    ) -> ToolExecutionOutcome:
        _ = decided_by

        try:
            tool = self._registry.get(tool_call.tool_name)
        except KeyError as exc:
            decision = PolicyDecision.deny(
                reason_code=REASON_TOOL_UNKNOWN,
                message="unknown tool",
                rule_id="policy_enforced_runner",
                metadata={"tool_name": tool_call.tool_name},
            )
            result = ToolResult(
                ok=False,
                error_code=POLICY_DENIED_ERROR_CODE,
                error_message=str(exc),
            )
            emit_decision_metric(metrics=self._metrics, decision=decision)
            log_decision(decision=decision, tool_call=tool_call)
            return ToolExecutionOutcome(
                tool_call_id=tool_call.tool_call_id,
                tool_name=tool_call.tool_name,
                decision=decision,
                result=result,
            )

        tool_spec = tool_spec_from_tool(tool)

        try:
            parsed_args: BaseModel = tool.ArgsModel.model_validate(tool_call.args)
        except ValidationError as exc:
            errors = exc.errors(include_input=False)
            decision = PolicyDecision.deny(
                reason_code=REASON_ARGS_INVALID,
                message="invalid tool args",
                rule_id="policy_enforced_runner",
                metadata={"tool_name": tool_call.tool_name, "errors": errors},
            )
            result = ToolResult(
                ok=False,
                error_code=POLICY_DENIED_ERROR_CODE,
                error_message="invalid tool args",
                debug={"errors": errors},
            )
            emit_decision_metric(metrics=self._metrics, decision=decision)
            log_decision(decision=decision, tool_call=tool_call)
            return ToolExecutionOutcome(
                tool_call_id=tool_call.tool_call_id,
                tool_name=tool_call.tool_name,
                decision=decision,
                result=result,
            )

        guard_decision = await self.evaluate_guards(
            tool_call=tool_call,
            tool_spec=tool_spec,
            parsed_args=parsed_args,
            approval_status=None,
            emit_metrics=True,
            now_ms=None,
        )

        if guard_decision.action == GuardAction.DELAY:
            result = ToolResult(
                ok=False,
                error_code=EXECUTION_DELAYED_ERROR_CODE,
                error_message=guard_decision.message or "execution delayed",
                debug=dict(guard_decision.metadata),
            )
            return ToolExecutionOutcome(
                tool_call_id=tool_call.tool_call_id,
                tool_name=tool_call.tool_name,
                decision=PolicyDecision.allow(
                    reason_code=guard_decision.reason_code,
                    message=guard_decision.message,
                    rule_id=guard_decision.guard_id,
                    metadata=dict(guard_decision.metadata),
                ),
                result=result,
                guard_decision=guard_decision,
            )

        decision = policy_decision_from_guard(guard_decision)
        if guard_decision.action == GuardAction.DENY:
            result = ToolResult(
                ok=False,
                error_code=POLICY_DENIED_ERROR_CODE,
                error_message=decision.message or f"policy denied: {decision.reason_code}",
            )
            log_decision(decision=decision, tool_call=tool_call)
            return ToolExecutionOutcome(
                tool_call_id=tool_call.tool_call_id,
                tool_name=tool_call.tool_name,
                decision=decision,
                result=result,
                guard_decision=guard_decision,
            )

        if guard_decision.action == GuardAction.REQUIRE_APPROVAL:
            return await handle_require_approval(
                self,
                tool_call=tool_call,
                tool_spec=tool_spec,
                parsed_args=parsed_args,
                ctx=ctx,
                required_decision=decision,
                require_approval_guard=guard_decision,
                on_before_execute=on_before_execute,
            )

        if on_before_execute is not None:
            await on_before_execute()
        result = await self._runner.run_tool(tool_call.tool_name, tool_call.args, ctx=ctx)
        return ToolExecutionOutcome(
            tool_call_id=tool_call.tool_call_id,
            tool_name=tool_call.tool_name,
            decision=decision,
            result=result,
        )


__all__ = ["PolicyEnforcedToolRunner"]

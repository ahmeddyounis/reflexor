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
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from reflexor.domain.enums import ApprovalStatus
from reflexor.domain.models import ToolCall
from reflexor.guards import GuardAction, GuardChain, GuardDecision
from reflexor.guards.defaults import build_default_policy_guard_chain
from reflexor.observability.metrics import ReflexorMetrics
from reflexor.security.policy.approvals import ApprovalBuilder, ApprovalStore
from reflexor.security.policy.context import PolicyContext, ToolSpec, tool_spec_from_tool
from reflexor.security.policy.decision import (
    REASON_APPROVAL_DENIED,
    REASON_APPROVED_OVERRIDE,
    REASON_ARGS_INVALID,
    REASON_TOOL_UNKNOWN,
    PolicyDecision,
)
from reflexor.security.policy.enforcement.guards import (
    evaluate_guards as _evaluate_guards,
)
from reflexor.security.policy.enforcement.guards import (
    policy_decision_from_guard,
)
from reflexor.security.policy.enforcement.telemetry import (
    emit_decision_metric,
    emit_guard_metrics,
    log_decision,
)
from reflexor.security.policy.enforcement.types import (
    APPROVAL_REQUIRED_ERROR_CODE,
    EXECUTION_DELAYED_ERROR_CODE,
    POLICY_DENIED_ERROR_CODE,
    ToolExecutionOutcome,
)
from reflexor.security.policy.enforcement.utils import _coerce_uuid4_str
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
            existing = await self._approvals.get_by_tool_call(tool_call.tool_call_id)
            if existing is not None:
                expected_hash, _ = self._approval_builder.build_payload_hash_for_args(
                    args=tool_call.args
                )
                if existing.payload_hash is not None and existing.payload_hash != expected_hash:
                    mismatch = PolicyDecision.deny(
                        reason_code=REASON_ARGS_INVALID,
                        message="approval payload_hash does not match tool_call args",
                        rule_id="policy_enforced_runner",
                        metadata={
                            "tool_name": tool_call.tool_name,
                            "approval_id": existing.approval_id,
                        },
                    )
                    result = ToolResult(
                        ok=False,
                        error_code=POLICY_DENIED_ERROR_CODE,
                        error_message="approval does not match current tool_call args",
                        data={"approval_id": existing.approval_id},
                    )
                    emit_decision_metric(metrics=self._metrics, decision=mismatch)
                    log_decision(
                        decision=mismatch,
                        tool_call=tool_call,
                        approval_id=existing.approval_id,
                        approval_status=existing.status,
                    )
                    return ToolExecutionOutcome(
                        tool_call_id=tool_call.tool_call_id,
                        tool_name=tool_call.tool_name,
                        decision=mismatch,
                        result=result,
                        approval_id=existing.approval_id,
                        approval_status=existing.status,
                        guard_decision=guard_decision,
                    )

                if existing.status == ApprovalStatus.APPROVED:
                    post_approval_guard = await self.evaluate_guards(
                        tool_call=tool_call,
                        tool_spec=tool_spec,
                        parsed_args=parsed_args,
                        approval_status=ApprovalStatus.APPROVED,
                        emit_metrics=False,
                        now_ms=None,
                    )
                    if post_approval_guard.action == GuardAction.DENY:
                        denied = policy_decision_from_guard(post_approval_guard)
                        result = ToolResult(
                            ok=False,
                            error_code=POLICY_DENIED_ERROR_CODE,
                            error_message=denied.message or f"policy denied: {denied.reason_code}",
                            data={"approval_id": existing.approval_id},
                        )
                        log_decision(
                            decision=denied,
                            tool_call=tool_call,
                            approval_id=existing.approval_id,
                            approval_status=existing.status,
                        )
                        return ToolExecutionOutcome(
                            tool_call_id=tool_call.tool_call_id,
                            tool_name=tool_call.tool_name,
                            decision=denied,
                            result=result,
                            approval_id=existing.approval_id,
                            approval_status=existing.status,
                            guard_decision=post_approval_guard,
                        )
                    if post_approval_guard.action == GuardAction.DELAY:
                        result = ToolResult(
                            ok=False,
                            error_code=EXECUTION_DELAYED_ERROR_CODE,
                            error_message=post_approval_guard.message or "execution delayed",
                            debug=dict(post_approval_guard.metadata),
                            data={"approval_id": existing.approval_id},
                        )
                        return ToolExecutionOutcome(
                            tool_call_id=tool_call.tool_call_id,
                            tool_name=tool_call.tool_name,
                            decision=PolicyDecision.allow(
                                reason_code=post_approval_guard.reason_code,
                                message=post_approval_guard.message,
                                rule_id=post_approval_guard.guard_id,
                                metadata=dict(post_approval_guard.metadata),
                            ),
                            result=result,
                            approval_id=existing.approval_id,
                            approval_status=existing.status,
                            guard_decision=post_approval_guard,
                        )

                    override = PolicyDecision.allow(
                        reason_code=REASON_APPROVED_OVERRIDE,
                        message="approval approved",
                        rule_id="policy_enforced_runner",
                        metadata={
                            **decision.metadata,
                            "approval_id": existing.approval_id,
                            "required_reason_code": decision.reason_code,
                            "required_rule_id": decision.rule_id,
                        },
                    )
                    emit_decision_metric(metrics=self._metrics, decision=override)
                    if on_before_execute is not None:
                        await on_before_execute()
                    result = await self._runner.run_tool(
                        tool_call.tool_name,
                        tool_call.args,
                        ctx=ctx,
                    )
                    return ToolExecutionOutcome(
                        tool_call_id=tool_call.tool_call_id,
                        tool_name=tool_call.tool_name,
                        decision=override,
                        result=result,
                        approval_id=existing.approval_id,
                        approval_status=existing.status,
                    )

                if existing.status == ApprovalStatus.DENIED:
                    override = PolicyDecision.deny(
                        reason_code=REASON_APPROVAL_DENIED,
                        message="approval denied",
                        rule_id="policy_enforced_runner",
                        metadata={
                            **decision.metadata,
                            "approval_id": existing.approval_id,
                            "required_reason_code": decision.reason_code,
                            "required_rule_id": decision.rule_id,
                        },
                    )
                    result = ToolResult(
                        ok=False,
                        error_code=POLICY_DENIED_ERROR_CODE,
                        error_message="approval denied",
                        data={"approval_id": existing.approval_id},
                    )
                    emit_decision_metric(metrics=self._metrics, decision=override)
                    log_decision(
                        decision=override,
                        tool_call=tool_call,
                        approval_id=existing.approval_id,
                        approval_status=existing.status,
                    )
                    return ToolExecutionOutcome(
                        tool_call_id=tool_call.tool_call_id,
                        tool_name=tool_call.tool_name,
                        decision=override,
                        result=result,
                        approval_id=existing.approval_id,
                        approval_status=existing.status,
                        guard_decision=guard_decision,
                    )

                result = ToolResult(
                    ok=False,
                    error_code=APPROVAL_REQUIRED_ERROR_CODE,
                    error_message="approval required",
                    data={"approval_id": existing.approval_id},
                )
                log_decision(
                    decision=decision,
                    tool_call=tool_call,
                    approval_id=existing.approval_id,
                    approval_status=existing.status,
                )
                return ToolExecutionOutcome(
                    tool_call_id=tool_call.tool_call_id,
                    tool_name=tool_call.tool_name,
                    decision=decision,
                    result=result,
                    approval_id=existing.approval_id,
                    approval_status=existing.status,
                    guard_decision=guard_decision,
                )

            run_id = _coerce_uuid4_str(ctx.correlation_ids.get("run_id")) or str(uuid4())
            task_id = _coerce_uuid4_str(ctx.correlation_ids.get("task_id")) or str(uuid4())

            attempted = self._approval_builder.build_pending(
                run_id=run_id,
                task_id=task_id,
                tool_call=tool_call,
                tool_spec=tool_spec,
                parsed_args=parsed_args,
                decision=decision,
            )
            created = await self._approvals.create_pending(attempted)
            if (
                self._metrics is not None
                and created.approval_id == attempted.approval_id
                and created.status == ApprovalStatus.PENDING
            ):
                self._metrics.approvals_pending_total.inc()

            if created.status == ApprovalStatus.APPROVED:
                post_approval_guard = await self.evaluate_guards(
                    tool_call=tool_call,
                    tool_spec=tool_spec,
                    parsed_args=parsed_args,
                    approval_status=ApprovalStatus.APPROVED,
                    emit_metrics=False,
                    now_ms=None,
                )
                if post_approval_guard.action == GuardAction.DENY:
                    denied = policy_decision_from_guard(post_approval_guard)
                    result = ToolResult(
                        ok=False,
                        error_code=POLICY_DENIED_ERROR_CODE,
                        error_message=denied.message or f"policy denied: {denied.reason_code}",
                        data={"approval_id": created.approval_id},
                    )
                    log_decision(
                        decision=denied,
                        tool_call=tool_call,
                        approval_id=created.approval_id,
                        approval_status=created.status,
                    )
                    return ToolExecutionOutcome(
                        tool_call_id=tool_call.tool_call_id,
                        tool_name=tool_call.tool_name,
                        decision=denied,
                        result=result,
                        approval_id=created.approval_id,
                        approval_status=created.status,
                        guard_decision=post_approval_guard,
                    )
                if post_approval_guard.action == GuardAction.DELAY:
                    result = ToolResult(
                        ok=False,
                        error_code=EXECUTION_DELAYED_ERROR_CODE,
                        error_message=post_approval_guard.message or "execution delayed",
                        debug=dict(post_approval_guard.metadata),
                        data={"approval_id": created.approval_id},
                    )
                    return ToolExecutionOutcome(
                        tool_call_id=tool_call.tool_call_id,
                        tool_name=tool_call.tool_name,
                        decision=PolicyDecision.allow(
                            reason_code=post_approval_guard.reason_code,
                            message=post_approval_guard.message,
                            rule_id=post_approval_guard.guard_id,
                            metadata=dict(post_approval_guard.metadata),
                        ),
                        result=result,
                        approval_id=created.approval_id,
                        approval_status=created.status,
                        guard_decision=post_approval_guard,
                    )

                override = PolicyDecision.allow(
                    reason_code=REASON_APPROVED_OVERRIDE,
                    message="approval approved",
                    rule_id="policy_enforced_runner",
                    metadata={
                        **decision.metadata,
                        "approval_id": created.approval_id,
                        "required_reason_code": decision.reason_code,
                        "required_rule_id": decision.rule_id,
                    },
                )
                emit_decision_metric(metrics=self._metrics, decision=override)
                if on_before_execute is not None:
                    await on_before_execute()
                result = await self._runner.run_tool(tool_call.tool_name, tool_call.args, ctx=ctx)
                return ToolExecutionOutcome(
                    tool_call_id=tool_call.tool_call_id,
                    tool_name=tool_call.tool_name,
                    decision=override,
                    result=result,
                    approval_id=created.approval_id,
                    approval_status=created.status,
                )

            if created.status == ApprovalStatus.DENIED:
                override = PolicyDecision.deny(
                    reason_code=REASON_APPROVAL_DENIED,
                    message="approval denied",
                    rule_id="policy_enforced_runner",
                    metadata={
                        **decision.metadata,
                        "approval_id": created.approval_id,
                        "required_reason_code": decision.reason_code,
                        "required_rule_id": decision.rule_id,
                    },
                )
                result = ToolResult(
                    ok=False,
                    error_code=POLICY_DENIED_ERROR_CODE,
                    error_message="approval denied",
                    data={"approval_id": created.approval_id},
                )
                emit_decision_metric(metrics=self._metrics, decision=override)
                log_decision(
                    decision=override,
                    tool_call=tool_call,
                    approval_id=created.approval_id,
                    approval_status=created.status,
                )
                return ToolExecutionOutcome(
                    tool_call_id=tool_call.tool_call_id,
                    tool_name=tool_call.tool_name,
                    decision=override,
                    result=result,
                    approval_id=created.approval_id,
                    approval_status=created.status,
                    guard_decision=guard_decision,
                )

            result = ToolResult(
                ok=False,
                error_code=APPROVAL_REQUIRED_ERROR_CODE,
                error_message="approval required",
                data={"approval_id": created.approval_id},
            )
            log_decision(
                decision=decision,
                tool_call=tool_call,
                approval_id=created.approval_id,
                approval_status=created.status,
            )
            return ToolExecutionOutcome(
                tool_call_id=tool_call.tool_call_id,
                tool_name=tool_call.tool_name,
                decision=decision,
                result=result,
                approval_id=created.approval_id,
                approval_status=created.status,
                guard_decision=guard_decision,
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

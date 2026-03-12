from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol
from uuid import uuid4

from pydantic import BaseModel

from reflexor.domain.enums import ApprovalStatus
from reflexor.domain.models import ToolCall
from reflexor.guards import GuardAction, GuardDecision
from reflexor.observability.metrics import ReflexorMetrics
from reflexor.security.policy.approvals import ApprovalBuilder, ApprovalStore
from reflexor.security.policy.context import ToolSpec
from reflexor.security.policy.decision import (
    REASON_APPROVAL_DENIED,
    REASON_APPROVED_OVERRIDE,
    REASON_ARGS_INVALID,
    PolicyDecision,
)
from reflexor.security.policy.enforcement.guards import policy_decision_from_guard
from reflexor.security.policy.enforcement.telemetry import emit_decision_metric, log_decision
from reflexor.security.policy.enforcement.types import (
    APPROVAL_REQUIRED_ERROR_CODE,
    EXECUTION_DELAYED_ERROR_CODE,
    POLICY_DENIED_ERROR_CODE,
    ToolExecutionOutcome,
)
from reflexor.security.policy.enforcement.utils import _coerce_uuid4_str
from reflexor.tools.runner import ToolRunner
from reflexor.tools.sdk import ToolContext, ToolResult


class _ApprovalEnforcementService(Protocol):
    _approvals: ApprovalStore
    _approval_builder: ApprovalBuilder
    _metrics: ReflexorMetrics | None
    _runner: ToolRunner

    async def evaluate_guards(
        self,
        *,
        tool_call: ToolCall,
        tool_spec: ToolSpec,
        parsed_args: BaseModel,
        approval_status: ApprovalStatus | None = None,
        emit_metrics: bool = True,
        now_ms: int | None = None,
    ) -> GuardDecision: ...


async def handle_require_approval(
    service: _ApprovalEnforcementService,
    *,
    tool_call: ToolCall,
    tool_spec: ToolSpec,
    parsed_args: BaseModel,
    ctx: ToolContext,
    required_decision: PolicyDecision,
    require_approval_guard: GuardDecision,
    on_before_execute: Callable[[], Awaitable[None]] | None,
) -> ToolExecutionOutcome:
    existing = await service._approvals.get_by_tool_call(tool_call.tool_call_id)
    if existing is not None:
        expected_hash, _ = service._approval_builder.build_payload_hash_for_args(
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
            emit_decision_metric(metrics=service._metrics, decision=mismatch)
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
                guard_decision=require_approval_guard,
            )

        if existing.status == ApprovalStatus.APPROVED:
            return await _execute_approved(
                service,
                tool_call=tool_call,
                tool_spec=tool_spec,
                parsed_args=parsed_args,
                ctx=ctx,
                approval_id=existing.approval_id,
                approval_status=existing.status,
                required_decision=required_decision,
                on_before_execute=on_before_execute,
            )

        if existing.status == ApprovalStatus.DENIED:
            override = PolicyDecision.deny(
                reason_code=REASON_APPROVAL_DENIED,
                message="approval denied",
                rule_id="policy_enforced_runner",
                metadata={
                    **required_decision.metadata,
                    "approval_id": existing.approval_id,
                    "required_reason_code": required_decision.reason_code,
                    "required_rule_id": required_decision.rule_id,
                },
            )
            result = ToolResult(
                ok=False,
                error_code=POLICY_DENIED_ERROR_CODE,
                error_message="approval denied",
                data={"approval_id": existing.approval_id},
            )
            emit_decision_metric(metrics=service._metrics, decision=override)
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
                guard_decision=require_approval_guard,
            )

        result = ToolResult(
            ok=False,
            error_code=APPROVAL_REQUIRED_ERROR_CODE,
            error_message="approval required",
            data={"approval_id": existing.approval_id},
        )
        log_decision(
            decision=required_decision,
            tool_call=tool_call,
            approval_id=existing.approval_id,
            approval_status=existing.status,
        )
        return ToolExecutionOutcome(
            tool_call_id=tool_call.tool_call_id,
            tool_name=tool_call.tool_name,
            decision=required_decision,
            result=result,
            approval_id=existing.approval_id,
            approval_status=existing.status,
            guard_decision=require_approval_guard,
        )

    run_id = _coerce_uuid4_str(ctx.correlation_ids.get("run_id")) or str(uuid4())
    task_id = _coerce_uuid4_str(ctx.correlation_ids.get("task_id")) or str(uuid4())

    attempted = service._approval_builder.build_pending(
        run_id=run_id,
        task_id=task_id,
        tool_call=tool_call,
        tool_spec=tool_spec,
        parsed_args=parsed_args,
        decision=required_decision,
    )
    created = await service._approvals.create_pending(attempted)
    if (
        service._metrics is not None
        and created.approval_id == attempted.approval_id
        and created.status == ApprovalStatus.PENDING
    ):
        service._metrics.approvals_pending_total.inc()

    if created.status == ApprovalStatus.APPROVED:
        return await _execute_approved(
            service,
            tool_call=tool_call,
            tool_spec=tool_spec,
            parsed_args=parsed_args,
            ctx=ctx,
            approval_id=created.approval_id,
            approval_status=created.status,
            required_decision=required_decision,
            on_before_execute=on_before_execute,
        )

    if created.status == ApprovalStatus.DENIED:
        override = PolicyDecision.deny(
            reason_code=REASON_APPROVAL_DENIED,
            message="approval denied",
            rule_id="policy_enforced_runner",
            metadata={
                **required_decision.metadata,
                "approval_id": created.approval_id,
                "required_reason_code": required_decision.reason_code,
                "required_rule_id": required_decision.rule_id,
            },
        )
        result = ToolResult(
            ok=False,
            error_code=POLICY_DENIED_ERROR_CODE,
            error_message="approval denied",
            data={"approval_id": created.approval_id},
        )
        emit_decision_metric(metrics=service._metrics, decision=override)
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
            guard_decision=require_approval_guard,
        )

    result = ToolResult(
        ok=False,
        error_code=APPROVAL_REQUIRED_ERROR_CODE,
        error_message="approval required",
        data={"approval_id": created.approval_id},
    )
    log_decision(
        decision=required_decision,
        tool_call=tool_call,
        approval_id=created.approval_id,
        approval_status=created.status,
    )
    return ToolExecutionOutcome(
        tool_call_id=tool_call.tool_call_id,
        tool_name=tool_call.tool_name,
        decision=required_decision,
        result=result,
        approval_id=created.approval_id,
        approval_status=created.status,
        guard_decision=require_approval_guard,
    )


async def _execute_approved(
    service: _ApprovalEnforcementService,
    *,
    tool_call: ToolCall,
    tool_spec: ToolSpec,
    parsed_args: BaseModel,
    ctx: ToolContext,
    approval_id: str,
    approval_status: ApprovalStatus,
    required_decision: PolicyDecision,
    on_before_execute: Callable[[], Awaitable[None]] | None,
) -> ToolExecutionOutcome:
    post_approval_guard = await service.evaluate_guards(
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
            data={"approval_id": approval_id},
        )
        log_decision(
            decision=denied,
            tool_call=tool_call,
            approval_id=approval_id,
            approval_status=approval_status,
        )
        return ToolExecutionOutcome(
            tool_call_id=tool_call.tool_call_id,
            tool_name=tool_call.tool_name,
            decision=denied,
            result=result,
            approval_id=approval_id,
            approval_status=approval_status,
            guard_decision=post_approval_guard,
        )
    if post_approval_guard.action == GuardAction.DELAY:
        decision = PolicyDecision.allow(
            reason_code=post_approval_guard.reason_code,
            message=post_approval_guard.message,
            rule_id=post_approval_guard.guard_id,
            metadata=dict(post_approval_guard.metadata),
        )
        result = ToolResult(
            ok=False,
            error_code=EXECUTION_DELAYED_ERROR_CODE,
            error_message=post_approval_guard.message or "execution delayed",
            debug=dict(post_approval_guard.metadata),
            data={"approval_id": approval_id},
        )
        log_decision(
            decision=decision,
            tool_call=tool_call,
            approval_id=approval_id,
            approval_status=approval_status,
        )
        return ToolExecutionOutcome(
            tool_call_id=tool_call.tool_call_id,
            tool_name=tool_call.tool_name,
            decision=decision,
            result=result,
            approval_id=approval_id,
            approval_status=approval_status,
            guard_decision=post_approval_guard,
        )
    if post_approval_guard.action == GuardAction.REQUIRE_APPROVAL:
        still_required = PolicyDecision.require_approval(
            reason_code=post_approval_guard.reason_code,
            message=post_approval_guard.message,
            rule_id=post_approval_guard.guard_id,
            metadata={
                **dict(post_approval_guard.metadata),
                "approval_id": approval_id,
                "approval_status": approval_status.value,
            },
        )
        emit_decision_metric(metrics=service._metrics, decision=still_required)
        result = ToolResult(
            ok=False,
            error_code=APPROVAL_REQUIRED_ERROR_CODE,
            error_message=still_required.message or "approval required",
            data={"approval_id": approval_id},
        )
        log_decision(
            decision=still_required,
            tool_call=tool_call,
            approval_id=approval_id,
            approval_status=approval_status,
        )
        return ToolExecutionOutcome(
            tool_call_id=tool_call.tool_call_id,
            tool_name=tool_call.tool_name,
            decision=still_required,
            result=result,
            approval_id=approval_id,
            approval_status=approval_status,
            guard_decision=post_approval_guard,
        )

    override = PolicyDecision.allow(
        reason_code=REASON_APPROVED_OVERRIDE,
        message="approval approved",
        rule_id="policy_enforced_runner",
        metadata={
            **required_decision.metadata,
            "approval_id": approval_id,
            "required_reason_code": required_decision.reason_code,
            "required_rule_id": required_decision.rule_id,
        },
    )
    emit_decision_metric(metrics=service._metrics, decision=override)
    if on_before_execute is not None:
        await on_before_execute()
    result = await service._runner.run_tool(tool_call.tool_name, tool_call.args, ctx=ctx)
    log_decision(
        decision=override,
        tool_call=tool_call,
        approval_id=approval_id,
        approval_status=approval_status,
    )
    return ToolExecutionOutcome(
        tool_call_id=tool_call.tool_call_id,
        tool_name=tool_call.tool_name,
        decision=override,
        result=result,
        approval_id=approval_id,
        approval_status=approval_status,
    )

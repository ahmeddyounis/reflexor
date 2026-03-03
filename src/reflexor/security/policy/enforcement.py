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

from uuid import UUID, uuid4

import structlog
from pydantic import BaseModel, ConfigDict, ValidationError

from reflexor.domain.enums import ApprovalStatus
from reflexor.domain.models import ToolCall
from reflexor.observability.metrics import ReflexorMetrics
from reflexor.security.policy.approvals import ApprovalBuilder, ApprovalStore
from reflexor.security.policy.context import tool_spec_from_tool
from reflexor.security.policy.decision import (
    REASON_APPROVAL_DENIED,
    REASON_APPROVED_OVERRIDE,
    REASON_ARGS_INVALID,
    REASON_TOOL_UNKNOWN,
    PolicyAction,
    PolicyDecision,
)
from reflexor.security.policy.gate import PolicyGate
from reflexor.tools.registry import ToolRegistry
from reflexor.tools.runner import ToolRunner
from reflexor.tools.sdk import ToolContext, ToolResult

POLICY_DENIED_ERROR_CODE = "policy_denied"
APPROVAL_REQUIRED_ERROR_CODE = "approval_required"


_logger = structlog.get_logger(__name__)


class ToolExecutionOutcome(BaseModel):
    """Outcome of a tool-call execution attempt enforced by policy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_call_id: str
    tool_name: str
    decision: PolicyDecision
    result: ToolResult
    approval_id: str | None = None
    approval_status: ApprovalStatus | None = None


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
    ) -> None:
        self._registry = registry
        self._runner = runner
        self._gate = gate
        self._approvals = approvals
        self._approval_builder = approval_builder or ApprovalBuilder(settings=gate.settings)
        self._metrics = metrics

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

    def _emit_decision_metric(self, decision: PolicyDecision) -> None:
        if self._metrics is None:
            return
        self._metrics.policy_decisions_total.labels(
            action=decision.action.value,
            reason_code=decision.reason_code,
        ).inc()

    def _log_decision(
        self,
        *,
        decision: PolicyDecision,
        tool_call: ToolCall,
        approval_id: str | None = None,
        approval_status: ApprovalStatus | None = None,
    ) -> None:
        payload = {
            "tool_call_id": tool_call.tool_call_id,
            "tool_name": tool_call.tool_name,
            "permission_scope": tool_call.permission_scope,
            "approval_id": approval_id,
            "approval_status": None if approval_status is None else approval_status.value,
            "decision": decision.to_audit_dict(),
        }
        if decision.action == PolicyAction.DENY:
            _logger.warning("policy denied tool call", **payload)
        elif decision.action == PolicyAction.REQUIRE_APPROVAL:
            _logger.info("policy requires approval", **payload)

    async def execute_tool_call(
        self,
        tool_call: ToolCall,
        *,
        ctx: ToolContext,
        decided_by: str | None = None,
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
            self._emit_decision_metric(decision)
            self._log_decision(decision=decision, tool_call=tool_call)
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
            self._emit_decision_metric(decision)
            self._log_decision(decision=decision, tool_call=tool_call)
            return ToolExecutionOutcome(
                tool_call_id=tool_call.tool_call_id,
                tool_name=tool_call.tool_name,
                decision=decision,
                result=result,
            )

        decision = self._gate.evaluate(
            tool_call=tool_call, tool_spec=tool_spec, parsed_args=parsed_args
        )
        if decision.action == PolicyAction.DENY:
            result = ToolResult(
                ok=False,
                error_code=POLICY_DENIED_ERROR_CODE,
                error_message=decision.message or f"policy denied: {decision.reason_code}",
            )
            self._log_decision(decision=decision, tool_call=tool_call)
            return ToolExecutionOutcome(
                tool_call_id=tool_call.tool_call_id,
                tool_name=tool_call.tool_name,
                decision=decision,
                result=result,
            )

        if decision.action == PolicyAction.REQUIRE_APPROVAL:
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
                    self._emit_decision_metric(mismatch)
                    self._log_decision(
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
                    )

                if existing.status == ApprovalStatus.APPROVED:
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
                    self._emit_decision_metric(override)
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
                    self._emit_decision_metric(override)
                    self._log_decision(
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
                    )

                result = ToolResult(
                    ok=False,
                    error_code=APPROVAL_REQUIRED_ERROR_CODE,
                    error_message="approval required",
                    data={"approval_id": existing.approval_id},
                )
                self._log_decision(
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
            created = await self._approvals.create_pending(
                attempted
            )
            if (
                self._metrics is not None
                and created.approval_id == attempted.approval_id
                and created.status == ApprovalStatus.PENDING
            ):
                self._metrics.approvals_pending_total.inc()

            if created.status == ApprovalStatus.APPROVED:
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
                self._emit_decision_metric(override)
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
                self._emit_decision_metric(override)
                self._log_decision(
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
                )

            result = ToolResult(
                ok=False,
                error_code=APPROVAL_REQUIRED_ERROR_CODE,
                error_message="approval required",
                data={"approval_id": created.approval_id},
            )
            self._log_decision(
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
            )

        result = await self._runner.run_tool(tool_call.tool_name, tool_call.args, ctx=ctx)
        return ToolExecutionOutcome(
            tool_call_id=tool_call.tool_call_id,
            tool_name=tool_call.tool_name,
            decision=decision,
            result=result,
        )


def _coerce_uuid4_str(value: str | None) -> str | None:
    if value is None:
        return None
    trimmed = value.strip()
    if not trimmed:
        return None
    try:
        parsed = UUID(trimmed)
    except ValueError:
        return None
    if parsed.version != 4:
        return None
    return str(parsed)

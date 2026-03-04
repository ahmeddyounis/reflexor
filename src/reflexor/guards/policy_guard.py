from __future__ import annotations

from pydantic import BaseModel

from reflexor.domain.enums import ApprovalStatus
from reflexor.domain.models import ToolCall
from reflexor.guards.context import GuardContext
from reflexor.guards.decision import GuardAction, GuardDecision
from reflexor.security.policy.context import ToolSpec
from reflexor.security.policy.decision import REASON_APPROVED_OVERRIDE, PolicyAction
from reflexor.security.policy.gate import PolicyGate


class PolicyGuard:
    """Guard adapter for PolicyGate (allow/deny/require approval)."""

    def __init__(self, *, gate: PolicyGate) -> None:
        self._gate = gate

    async def check(
        self,
        tool_call: ToolCall,
        tool_spec: ToolSpec,
        parsed_args: BaseModel,
        ctx: GuardContext,
    ) -> GuardDecision:
        decision = self._gate.evaluate(
            tool_call=tool_call,
            tool_spec=tool_spec,
            parsed_args=parsed_args,
            ctx=ctx.policy,
            emit_metrics=bool(ctx.emit_metrics),
        )

        if (
            decision.action == PolicyAction.REQUIRE_APPROVAL
            and ctx.approval_status == ApprovalStatus.APPROVED
        ):
            return GuardDecision.allow(
                reason_code=REASON_APPROVED_OVERRIDE,
                message="approval approved",
                guard_id="guard.policy",
                metadata={
                    **decision.metadata,
                    "required_reason_code": decision.reason_code,
                    "required_rule_id": decision.rule_id,
                },
            )

        mapped_action = (
            GuardAction.ALLOW
            if decision.action == PolicyAction.ALLOW
            else GuardAction.DENY
            if decision.action == PolicyAction.DENY
            else GuardAction.REQUIRE_APPROVAL
        )
        return GuardDecision(
            action=mapped_action,
            reason_code=decision.reason_code,
            message=decision.message,
            guard_id=decision.rule_id,
            metadata=dict(decision.metadata),
        )


__all__ = ["PolicyGuard"]

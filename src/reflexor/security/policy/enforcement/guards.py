from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel

from reflexor.domain.enums import ApprovalStatus
from reflexor.domain.models import ToolCall
from reflexor.guards import GuardAction, GuardChain, GuardContext, GuardDecision
from reflexor.observability.context import get_correlation_ids
from reflexor.security.policy.context import PolicyContext, ToolSpec
from reflexor.security.policy.decision import PolicyDecision


async def evaluate_guards(
    *,
    guard_chain: GuardChain,
    policy_ctx: PolicyContext,
    tool_call: ToolCall,
    tool_spec: ToolSpec,
    parsed_args: BaseModel,
    approval_status: ApprovalStatus | None,
    emit_metrics: bool,
    now_ms: int | None,
    now_ms_func: Callable[[], int],
) -> GuardDecision:
    now_ms_value = int(now_ms_func()) if now_ms is None else int(now_ms)
    correlation_ids = get_correlation_ids()
    ctx = GuardContext(
        policy=policy_ctx,
        now_ms=now_ms_value,
        emit_metrics=bool(emit_metrics),
        approval_status=approval_status,
        run_id=correlation_ids.get("run_id"),
    )
    return await guard_chain.check(
        tool_call=tool_call,
        tool_spec=tool_spec,
        parsed_args=parsed_args,
        ctx=ctx,
    )


def policy_decision_from_guard(guard_decision: GuardDecision) -> PolicyDecision:
    if guard_decision.action == GuardAction.ALLOW:
        return PolicyDecision.allow(
            reason_code=guard_decision.reason_code,
            message=guard_decision.message,
            rule_id=guard_decision.guard_id,
            metadata=dict(guard_decision.metadata),
        )

    if guard_decision.action == GuardAction.DENY:
        return PolicyDecision.deny(
            reason_code=guard_decision.reason_code,
            message=guard_decision.message,
            rule_id=guard_decision.guard_id,
            metadata=dict(guard_decision.metadata),
        )

    if guard_decision.action == GuardAction.REQUIRE_APPROVAL:
        return PolicyDecision.require_approval(
            reason_code=guard_decision.reason_code,
            message=guard_decision.message,
            rule_id=guard_decision.guard_id,
            metadata=dict(guard_decision.metadata),
        )

    raise ValueError(f"cannot map guard action to policy decision: {guard_decision.action!r}")


__all__ = ["evaluate_guards", "policy_decision_from_guard"]

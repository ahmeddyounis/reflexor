from __future__ import annotations

import structlog

from reflexor.domain.enums import ApprovalStatus
from reflexor.domain.models import ToolCall
from reflexor.guards import GuardAction, GuardDecision
from reflexor.observability.metrics import ReflexorMetrics
from reflexor.security.policy.decision import PolicyAction, PolicyDecision

_logger = structlog.get_logger(__name__)


def emit_guard_metrics(
    *,
    metrics: ReflexorMetrics | None,
    tool_call: ToolCall,
    decision: GuardDecision,
    emit_metrics: bool,
) -> None:
    if not emit_metrics or metrics is None:
        return

    if decision.action != GuardAction.DELAY:
        return

    delay_s: float | None = None
    raw_delay_s = decision.metadata.get("delay_s")
    if isinstance(raw_delay_s, (int, float, str)):
        try:
            parsed = float(raw_delay_s)
        except (TypeError, ValueError):
            parsed = 0.0
        delay_s = max(0.0, parsed)

    if delay_s is not None:
        metrics.retry_after_seconds.labels(
            reason_code=decision.reason_code,
            tool_name=tool_call.tool_name,
        ).observe(delay_s)

    if decision.reason_code == "rate_limited":
        metrics.rate_limited_total.labels(tool_name=tool_call.tool_name).inc()
        return

    if decision.reason_code == "circuit_open":
        destination = ""
        raw_key = decision.metadata.get("circuit_key")
        if isinstance(raw_key, dict):
            raw_destination = raw_key.get("destination")
            if isinstance(raw_destination, str):
                destination = raw_destination
        metrics.circuit_open_total.labels(
            tool_name=tool_call.tool_name,
            destination=destination,
        ).inc()
        return


def emit_decision_metric(*, metrics: ReflexorMetrics | None, decision: PolicyDecision) -> None:
    if metrics is None:
        return
    metrics.policy_decisions_total.labels(
        action=decision.action.value,
        reason_code=decision.reason_code,
    ).inc()


def log_decision(
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


__all__ = ["emit_decision_metric", "emit_guard_metrics", "log_decision"]

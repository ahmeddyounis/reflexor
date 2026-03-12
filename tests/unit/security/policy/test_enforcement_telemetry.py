from __future__ import annotations

from structlog.testing import capture_logs

from reflexor.domain.models import ToolCall
from reflexor.security.policy.decision import REASON_APPROVED_OVERRIDE, PolicyDecision
from reflexor.security.policy.enforcement.telemetry import log_decision


def test_log_decision_logs_non_ok_allow_outcomes() -> None:
    tool_call = ToolCall(
        tool_name="tests.telemetry",
        permission_scope="fs.read",
        idempotency_key="k",
        args={},
    )
    decision = PolicyDecision.allow(
        reason_code=REASON_APPROVED_OVERRIDE,
        message="approval approved",
        rule_id="policy_enforced_runner",
        metadata={"approval_id": "123"},
    )

    with capture_logs() as logs:
        log_decision(decision=decision, tool_call=tool_call, approval_id="123")

    assert len(logs) == 1
    assert logs[0]["event"] == "policy allowed tool call with non-ok reason"
    assert logs[0]["decision"]["reason_code"] == REASON_APPROVED_OVERRIDE
    assert logs[0]["approval_id"] == "123"

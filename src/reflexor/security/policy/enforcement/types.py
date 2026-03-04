from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from reflexor.domain.enums import ApprovalStatus
from reflexor.guards.decision import GuardDecision
from reflexor.security.policy.decision import PolicyDecision
from reflexor.tools.sdk import ToolResult

POLICY_DENIED_ERROR_CODE = "policy_denied"
APPROVAL_REQUIRED_ERROR_CODE = "approval_required"
EXECUTION_DELAYED_ERROR_CODE = "execution_delayed"


class ToolExecutionOutcome(BaseModel):
    """Outcome of a tool-call execution attempt enforced by policy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_call_id: str
    tool_name: str
    decision: PolicyDecision
    result: ToolResult
    guard_decision: GuardDecision | None = None
    approval_id: str | None = None
    approval_status: ApprovalStatus | None = None

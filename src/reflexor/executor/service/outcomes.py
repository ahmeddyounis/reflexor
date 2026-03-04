from __future__ import annotations

from reflexor.domain.enums import ApprovalStatus, TaskStatus
from reflexor.domain.models import Task
from reflexor.executor.retries import (
    ErrorClassifier,
    RetryDisposition,
    RetryPolicy,
    exponential_backoff_s,
)
from reflexor.executor.service.types import ExecutionDisposition
from reflexor.security.policy.decision import PolicyAction
from reflexor.security.policy.enforcement import ToolExecutionOutcome


def did_attempt_tool_run(outcome: ToolExecutionOutcome) -> bool:
    if outcome.decision.action == PolicyAction.ALLOW:
        return True
    if outcome.decision.action == PolicyAction.REQUIRE_APPROVAL:
        return outcome.approval_status == ApprovalStatus.APPROVED
    return False


def classify_outcome(
    *, task: Task, outcome: ToolExecutionOutcome, retry_policy: RetryPolicy
) -> tuple[ExecutionDisposition, float | None]:
    if task.status == TaskStatus.CANCELED:
        return ExecutionDisposition.CANCELED, None

    if outcome.approval_status == ApprovalStatus.DENIED:
        return ExecutionDisposition.DENIED, None

    if outcome.decision.action == PolicyAction.DENY:
        return ExecutionDisposition.DENIED, None

    if outcome.result.ok:
        return ExecutionDisposition.SUCCEEDED, None

    classifier = ErrorClassifier(policy=retry_policy)
    disposition = classifier.classify(outcome.result)
    if disposition == RetryDisposition.APPROVAL_REQUIRED:
        return ExecutionDisposition.WAITING_APPROVAL, None

    if disposition == RetryDisposition.TRANSIENT:
        attempt = max(1, int(task.attempts))
        retry_after_s = exponential_backoff_s(
            attempt,
            base_delay_s=retry_policy.base_delay_s,
            max_delay_s=retry_policy.max_delay_s,
        )
        return ExecutionDisposition.FAILED_TRANSIENT, retry_after_s

    return ExecutionDisposition.FAILED_PERMANENT, None

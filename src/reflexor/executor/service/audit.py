from __future__ import annotations

from uuid import uuid4

from reflexor.config import ReflexorSettings
from reflexor.domain.enums import ApprovalStatus, TaskStatus
from reflexor.domain.models import Task, ToolCall
from reflexor.domain.models_event import Event
from reflexor.domain.models_run_packet import RunPacket
from reflexor.executor.service.types import ExecutionDisposition, RunPacketPersistError
from reflexor.guards.decision import GuardDecision
from reflexor.observability.audit_sanitize import sanitize_tool_output
from reflexor.security.policy.decision import PolicyDecision
from reflexor.storage.ports import RunPacketRepo
from reflexor.tools.sdk import ToolResult

DEFAULT_MAX_EXECUTION_RESULT_SUMMARY_BYTES = 8_000


def new_fallback_packet(*, task: Task, now_ms: int) -> RunPacket:
    packet_created_at_ms = min(
        [
            now_ms,
            int(task.created_at_ms),
            *([] if task.started_at_ms is None else [int(task.started_at_ms)]),
            *([] if task.completed_at_ms is None else [int(task.completed_at_ms)]),
        ]
    )
    return RunPacket(
        run_id=task.run_id,
        event=Event(
            event_id=str(uuid4()),
            type="executor.task",
            source="executor",
            received_at_ms=now_ms,
            payload={"task_id": task.task_id, "run_id": task.run_id},
        ),
        tasks=[task],
        created_at_ms=packet_created_at_ms,
    )


async def append_audit(
    *,
    run_packet_repo: RunPacketRepo,
    task: Task,
    tool_call: ToolCall,
    decision: PolicyDecision,
    result: ToolResult,
    disposition: ExecutionDisposition,
    retry_after_s: float | None,
    will_retry: bool,
    approval_id: str | None,
    approval_status: ApprovalStatus | None,
    now_ms: int,
    settings: ReflexorSettings,
    guard_decision: GuardDecision | None = None,
) -> None:
    packet = await run_packet_repo.get(task.run_id)
    if packet is None:
        packet = new_fallback_packet(task=task, now_ms=now_ms)

    summary_budget = min(
        int(settings.max_tool_output_bytes),
        DEFAULT_MAX_EXECUTION_RESULT_SUMMARY_BYTES,
    )
    summary_settings = settings.model_copy(update={"max_tool_output_bytes": summary_budget})

    tool_result_entry: dict[str, object] = {
        "task_id": task.task_id,
        "tool_call_id": tool_call.tool_call_id,
        "tool_name": tool_call.tool_name,
        "status": disposition.value,
        "error_code": result.error_code,
        "retry": {
            "will_retry": bool(will_retry),
            "retry_after_s": retry_after_s,
            "attempt": int(task.attempts),
            "max_attempts": int(task.max_attempts),
        },
        "policy_decision": {
            "action": decision.action.value,
            "reason_code": decision.reason_code,
            "rule_id": decision.rule_id,
        },
        "result_summary": sanitize_tool_output(
            result.model_dump(mode="json"), settings=summary_settings
        ),
        "approval_id": approval_id,
        "approval_status": None if approval_status is None else approval_status.value,
        "recorded_at_ms": now_ms,
    }
    if guard_decision is not None:
        tool_result_entry["guard_decision"] = guard_decision.model_dump(mode="json")

    decision_entry: dict[str, object] = {
        "task_id": task.task_id,
        "tool_call_id": tool_call.tool_call_id,
        **decision.to_audit_dict(),
    }

    updated = packet.with_task_upserted(task)
    if updated.started_at_ms is None and task.started_at_ms is not None:
        updated = updated.model_copy(update={"started_at_ms": task.started_at_ms}, deep=True)

    all_tasks_terminal = bool(updated.tasks) and all(
        candidate.status in {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELED}
        for candidate in updated.tasks
    )
    if all_tasks_terminal:
        updated = updated.model_copy(update={"completed_at_ms": now_ms}, deep=True)

    updated = updated.with_tool_result_added(tool_result_entry).with_policy_decision_added(
        decision_entry
    )

    try:
        await run_packet_repo.create(updated)
    except Exception as exc:  # pragma: no cover
        raise RunPacketPersistError("failed to persist run packet") from exc

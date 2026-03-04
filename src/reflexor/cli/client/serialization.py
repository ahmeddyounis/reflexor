from __future__ import annotations

from reflexor.application.services import SubmitEventOutcome
from reflexor.storage.ports import EventSuppressionRecord as StoredEventSuppressionRecord
from reflexor.storage.ports import RunSummary as StoredRunSummary
from reflexor.storage.ports import TaskSummary as StoredTaskSummary


def _page(
    *,
    limit: int,
    offset: int,
    total: int,
    items: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "limit": int(limit),
        "offset": int(offset),
        "total": int(total),
        "items": items,
    }


def _run_summary_to_dict(summary: StoredRunSummary) -> dict[str, object]:
    return {
        "run_id": summary.run_id,
        "created_at_ms": int(summary.created_at_ms),
        "started_at_ms": summary.started_at_ms,
        "completed_at_ms": summary.completed_at_ms,
        "status": str(summary.status),
        "event_type": summary.event_type,
        "event_source": summary.event_source,
        "tasks_total": int(summary.tasks_total),
        "tasks_pending": int(summary.tasks_pending),
        "tasks_queued": int(summary.tasks_queued),
        "tasks_running": int(summary.tasks_running),
        "tasks_succeeded": int(summary.tasks_succeeded),
        "tasks_failed": int(summary.tasks_failed),
        "tasks_canceled": int(summary.tasks_canceled),
        "approvals_total": int(summary.approvals_total),
        "approvals_pending": int(summary.approvals_pending),
    }


def _task_summary_to_dict(summary: StoredTaskSummary) -> dict[str, object]:
    return {
        "task_id": summary.task_id,
        "run_id": summary.run_id,
        "name": summary.name,
        "status": str(summary.status),
        "attempts": int(summary.attempts),
        "max_attempts": int(summary.max_attempts),
        "timeout_s": int(summary.timeout_s),
        "depends_on": list(summary.depends_on),
        "tool_call_id": summary.tool_call_id,
        "tool_name": summary.tool_name,
        "permission_scope": summary.permission_scope,
        "idempotency_key": summary.idempotency_key,
        "tool_call_status": (
            None if summary.tool_call_status is None else str(summary.tool_call_status)
        ),
    }


def _submit_outcome_to_dict(outcome: SubmitEventOutcome) -> dict[str, object]:
    return {
        "ok": True,
        "event_id": outcome.event_id,
        "run_id": outcome.run_id,
        "duplicate": bool(outcome.duplicate),
    }


def _suppression_to_dict(record: StoredEventSuppressionRecord) -> dict[str, object]:
    return {
        "signature_hash": record.signature_hash,
        "event_type": record.event_type,
        "event_source": record.event_source,
        "signature": record.signature,
        "count": int(record.count),
        "threshold": int(record.threshold),
        "window_ms": int(record.window_ms),
        "window_start_ms": int(record.window_start_ms),
        "suppressed_until_ms": record.suppressed_until_ms,
        "expires_at_ms": int(record.expires_at_ms),
        "resume_required": bool(record.resume_required),
        "cleared_at_ms": record.cleared_at_ms,
        "cleared_by": record.cleared_by,
        "cleared_request_id": record.cleared_request_id,
        "created_at_ms": int(record.created_at_ms),
        "updated_at_ms": int(record.updated_at_ms),
    }


__all__ = [
    "_page",
    "_run_summary_to_dict",
    "_suppression_to_dict",
    "_submit_outcome_to_dict",
    "_task_summary_to_dict",
]

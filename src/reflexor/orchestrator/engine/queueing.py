from __future__ import annotations

import time
from collections.abc import Sequence
from typing import TYPE_CHECKING

from reflexor.domain.enums import TaskStatus
from reflexor.domain.models import Task
from reflexor.observability.context import correlation_context, get_correlation_ids
from reflexor.observability.tracing import inject_trace_carrier
from reflexor.orchestrator.engine.types import PlanningTrigger
from reflexor.orchestrator.queue import TaskEnvelope
from reflexor.orchestrator.validation import PlanValidationError

if TYPE_CHECKING:
    from reflexor.orchestrator.engine.core import OrchestratorEngine


class TaskEnqueueError(RuntimeError):
    """Raised when queueing fails after some tasks may already have been enqueued."""

    def __init__(
        self,
        message: str,
        *,
        enqueued_task_ids: Sequence[str],
        failed_task_id: str,
        failed_tool_call_id: str | None,
    ) -> None:
        super().__init__(message)
        self.enqueued_task_ids = list(enqueued_task_ids)
        self.failed_task_id = failed_task_id
        self.failed_tool_call_id = failed_tool_call_id


async def enqueue_tasks(
    engine: OrchestratorEngine,
    tasks: Sequence[Task],
    *,
    reason: str,
    source: str,
    trigger: PlanningTrigger | None = None,
    first_enqueue_started_s: float | None = None,
) -> list[str]:
    now_ms = int(engine.clock.now_ms())
    ready_tasks = [task for task in tasks if not task.depends_on]
    enqueued_task_ids: list[str] = []
    for idx, task in enumerate(ready_tasks):
        tool_call = task.tool_call
        if tool_call is None:
            raise PlanValidationError("task.tool_call is required for queueing")

        with correlation_context(task_id=task.task_id, tool_call_id=tool_call.tool_call_id):
            trace_payload: dict[str, object] = {
                "reason": reason,
                "source": source,
                "trigger": trigger,
            }
            otel_carrier = inject_trace_carrier()
            if otel_carrier:
                trace_payload["otel"] = otel_carrier
            envelope = TaskEnvelope(
                task_id=task.task_id,
                run_id=task.run_id,
                attempt=task.attempts,
                created_at_ms=now_ms,
                available_at_ms=now_ms,
                priority=_task_priority(task),
                correlation_ids=get_correlation_ids(),
                trace=trace_payload,
                payload={
                    "tool_call_id": tool_call.tool_call_id,
                    "tool_name": tool_call.tool_name,
                    "permission_scope": tool_call.permission_scope,
                    "idempotency_key": tool_call.idempotency_key,
                },
            )
            try:
                await engine.queue.enqueue(envelope)
            except Exception as exc:
                raise TaskEnqueueError(
                    f"failed to enqueue task {task.task_id!r}",
                    enqueued_task_ids=enqueued_task_ids,
                    failed_task_id=task.task_id,
                    failed_tool_call_id=tool_call.tool_call_id,
                ) from exc
            enqueued_task_ids.append(task.task_id)
            if (
                idx == 0
                and first_enqueue_started_s is not None
                and engine.metrics is not None
                and source == "reflex"
            ):
                engine.metrics.event_to_enqueue_seconds.observe(
                    time.perf_counter() - first_enqueue_started_s
                )
                first_enqueue_started_s = None
    return enqueued_task_ids


def mark_enqueued_tasks(tasks: Sequence[Task], enqueued_task_ids: Sequence[str]) -> list[Task]:
    if not enqueued_task_ids:
        return list(tasks)

    enqueued_set = set(enqueued_task_ids)
    return [
        (
            task.model_copy(update={"status": TaskStatus.QUEUED}, deep=True)
            if task.task_id in enqueued_set
            else task
        )
        for task in tasks
    ]


def _task_priority(task: Task) -> int | None:
    planner_metadata = task.metadata.get("planner")
    if not isinstance(planner_metadata, dict):
        return None

    priority = planner_metadata.get("priority")
    if isinstance(priority, bool) or not isinstance(priority, int):
        return None
    if priority < 0:
        return None
    return priority

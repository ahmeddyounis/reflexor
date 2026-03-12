from __future__ import annotations

from collections import deque
from collections.abc import Sequence

from reflexor.domain.enums import TaskStatus
from reflexor.domain.models import Task
from reflexor.observability.context import correlation_context, get_correlation_ids
from reflexor.observability.tracing import inject_trace_carrier
from reflexor.orchestrator.queue import TaskEnvelope


def has_unmet_dependencies(*, task: Task, all_tasks: Sequence[Task]) -> bool:
    tasks_by_id = {candidate.task_id: candidate for candidate in all_tasks}
    for dependency_id in task.depends_on:
        dependency = tasks_by_id.get(dependency_id)
        if dependency is None:
            return True
        if dependency.status != TaskStatus.SUCCEEDED:
            return True
    return False


def ready_dependents_after_success(*, task: Task, all_tasks: Sequence[Task]) -> list[Task]:
    tasks_by_id = {candidate.task_id: candidate for candidate in all_tasks}
    ready: list[Task] = []

    for candidate in all_tasks:
        if candidate.status != TaskStatus.PENDING:
            continue
        if task.task_id not in candidate.depends_on:
            continue
        if all(
            tasks_by_id.get(dependency_id) is not None
            and tasks_by_id[dependency_id].status == TaskStatus.SUCCEEDED
            for dependency_id in candidate.depends_on
        ):
            ready.append(candidate)

    return ready


def blocked_dependents_after_failure(*, task: Task, all_tasks: Sequence[Task]) -> list[Task]:
    pending_by_id = {
        candidate.task_id: candidate
        for candidate in all_tasks
        if candidate.status == TaskStatus.PENDING
    }
    dependents_by_task_id: dict[str, list[Task]] = {}
    for candidate in pending_by_id.values():
        for dependency_id in candidate.depends_on:
            dependents_by_task_id.setdefault(dependency_id, []).append(candidate)

    blocked: list[Task] = []
    queue: deque[str] = deque([task.task_id])
    seen: set[str] = set()
    blocked_ids: set[str] = set()
    while queue:
        dependency_id = queue.popleft()
        if dependency_id in seen:
            continue
        seen.add(dependency_id)
        for dependent in dependents_by_task_id.get(dependency_id, []):
            if dependent.task_id in seen or dependent.task_id in blocked_ids:
                continue
            blocked.append(dependent)
            blocked_ids.add(dependent.task_id)
            queue.append(dependent.task_id)
    return blocked


def dependency_ready_envelope(
    *,
    task: Task,
    now_ms: int,
    upstream_task_id: str,
) -> TaskEnvelope:
    tool_call = task.tool_call
    if tool_call is None:
        raise ValueError("dependency_ready_envelope requires task.tool_call")

    with correlation_context(
        run_id=task.run_id,
        task_id=task.task_id,
        tool_call_id=tool_call.tool_call_id,
    ):
        trace_payload: dict[str, object] = {
            "reason": "dependency_satisfied",
            "source": "executor",
            "upstream_task_id": upstream_task_id,
        }
        otel_carrier = inject_trace_carrier()
        if otel_carrier:
            trace_payload["otel"] = otel_carrier
        return TaskEnvelope(
            task_id=task.task_id,
            run_id=task.run_id,
            attempt=int(task.attempts),
            created_at_ms=now_ms,
            available_at_ms=now_ms,
            correlation_ids=get_correlation_ids(),
            trace=trace_payload,
            payload={
                "tool_call_id": tool_call.tool_call_id,
                "tool_name": tool_call.tool_name,
                "permission_scope": tool_call.permission_scope,
                "idempotency_key": tool_call.idempotency_key,
                "upstream_task_id": upstream_task_id,
            },
        )


__all__ = [
    "blocked_dependents_after_failure",
    "dependency_ready_envelope",
    "has_unmet_dependencies",
    "ready_dependents_after_success",
]

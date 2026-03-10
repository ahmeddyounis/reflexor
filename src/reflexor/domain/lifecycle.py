from __future__ import annotations

from typing import NoReturn, overload

from reflexor.domain.enums import TaskStatus, ToolCallStatus
from reflexor.domain.errors import InvalidTransition
from reflexor.domain.models import Task, ToolCall

TASK_ALLOWED_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.PENDING: frozenset({TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.CANCELED}),
    TaskStatus.QUEUED: frozenset(
        {TaskStatus.WAITING_APPROVAL, TaskStatus.RUNNING, TaskStatus.CANCELED}
    ),
    TaskStatus.RUNNING: frozenset(
        {TaskStatus.WAITING_APPROVAL, TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELED}
    ),
    TaskStatus.WAITING_APPROVAL: frozenset({TaskStatus.QUEUED, TaskStatus.CANCELED}),
    TaskStatus.FAILED: frozenset({TaskStatus.RUNNING, TaskStatus.CANCELED, TaskStatus.ARCHIVED}),
    TaskStatus.SUCCEEDED: frozenset({TaskStatus.ARCHIVED}),
    TaskStatus.CANCELED: frozenset({TaskStatus.ARCHIVED}),
    TaskStatus.ARCHIVED: frozenset(),
}

TOOL_CALL_ALLOWED_TRANSITIONS: dict[ToolCallStatus, frozenset[ToolCallStatus]] = {
    ToolCallStatus.PENDING: frozenset(
        {ToolCallStatus.RUNNING, ToolCallStatus.DENIED, ToolCallStatus.CANCELED}
    ),
    ToolCallStatus.RUNNING: frozenset(
        {ToolCallStatus.SUCCEEDED, ToolCallStatus.FAILED, ToolCallStatus.CANCELED}
    ),
    ToolCallStatus.FAILED: frozenset({ToolCallStatus.RUNNING, ToolCallStatus.CANCELED}),
    ToolCallStatus.SUCCEEDED: frozenset(),
    ToolCallStatus.DENIED: frozenset(),
    ToolCallStatus.CANCELED: frozenset(),
}


@overload
def can_transition(current: TaskStatus, target: TaskStatus) -> bool: ...


@overload
def can_transition(current: ToolCallStatus, target: ToolCallStatus) -> bool: ...


def can_transition(current: object, target: object) -> bool:
    if isinstance(current, TaskStatus) and isinstance(target, TaskStatus):
        return target in TASK_ALLOWED_TRANSITIONS[current]
    if isinstance(current, ToolCallStatus) and isinstance(target, ToolCallStatus):
        return target in TOOL_CALL_ALLOWED_TRANSITIONS[current]
    raise TypeError("can_transition expects TaskStatus→TaskStatus or ToolCallStatus→ToolCallStatus")


@overload
def transition(entity: Task, target: TaskStatus) -> Task: ...


@overload
def transition(entity: ToolCall, target: ToolCallStatus) -> ToolCall: ...


def transition(entity: Task | ToolCall, target: TaskStatus | ToolCallStatus) -> Task | ToolCall:
    if isinstance(entity, Task):
        if not isinstance(target, TaskStatus):
            raise TypeError("Task transition target must be a TaskStatus")
        return transition_task(entity, target)

    if isinstance(entity, ToolCall):
        if not isinstance(target, ToolCallStatus):
            raise TypeError("ToolCall transition target must be a ToolCallStatus")
        return transition_tool_call(entity, target)

    raise TypeError("transition expects a Task or ToolCall")


def transition_task(task: Task, target: TaskStatus) -> Task:
    current = task.status
    if not can_transition(current, target):
        raise InvalidTransition(
            "invalid task status transition",
            current_state=current.value,
            requested_state=target.value,
            context={"entity": "Task"},
        )

    updated = task.model_dump()
    updated["status"] = target

    if target == TaskStatus.RUNNING and current in {
        TaskStatus.PENDING,
        TaskStatus.QUEUED,
        TaskStatus.FAILED,
    }:
        if task.attempts >= task.max_attempts:
            raise InvalidTransition(
                "max_attempts reached; cannot retry task",
                current_state=current.value,
                requested_state=target.value,
                context={
                    "entity": "Task",
                    "attempts": task.attempts,
                    "max_attempts": task.max_attempts,
                },
            )
        updated["attempts"] = task.attempts + 1
        updated["completed_at_ms"] = None

    transitioned = Task.model_validate(updated)
    _validate_task_invariants(transitioned, current_state=current)
    return transitioned


def transition_tool_call(tool_call: ToolCall, target: ToolCallStatus) -> ToolCall:
    current = tool_call.status
    if not can_transition(current, target):
        raise InvalidTransition(
            "invalid tool call status transition",
            current_state=current.value,
            requested_state=target.value,
            context={"entity": "ToolCall"},
        )

    updated = tool_call.model_dump()
    updated["status"] = target

    if target == ToolCallStatus.RUNNING and current in {
        ToolCallStatus.PENDING,
        ToolCallStatus.FAILED,
    }:
        updated["completed_at_ms"] = None

    transitioned = ToolCall.model_validate(updated)
    _validate_tool_call_invariants(transitioned, current_state=current)
    return transitioned


def _validate_task_invariants(task: Task, *, current_state: TaskStatus) -> None:
    status = task.status
    ctx = {"entity": "Task"}

    def fail(reason: str, **extra: object) -> NoReturn:
        raise InvalidTransition(
            reason,
            current_state=current_state.value,
            requested_state=status.value,
            context={**ctx, **extra},
        )

    if status == TaskStatus.PENDING:
        if task.started_at_ms is not None or task.completed_at_ms is not None:
            fail("pending task cannot have started_at_ms/completed_at_ms set")
        return

    if status == TaskStatus.QUEUED:
        if task.tool_call is None:
            fail("queued task must have tool_call")
        if task.tool_call.status != ToolCallStatus.PENDING:
            fail(
                "queued task must have pending tool_call",
                tool_call_status=task.tool_call.status.value,
            )
        if task.started_at_ms is not None or task.completed_at_ms is not None:
            fail("queued task cannot have started_at_ms/completed_at_ms set")
        return

    if status == TaskStatus.WAITING_APPROVAL:
        if task.tool_call is None:
            fail("waiting approval task must have tool_call")
        if task.tool_call.status not in {ToolCallStatus.PENDING, ToolCallStatus.RUNNING}:
            fail(
                "waiting approval task must have pending/running tool_call",
                tool_call_status=task.tool_call.status.value,
            )
        if task.completed_at_ms is not None:
            fail("waiting approval task must not have completed_at_ms set")
        if task.tool_call.completed_at_ms is not None:
            fail(
                "waiting approval task must not have completed tool_call",
                tool_call_completed_at_ms=task.tool_call.completed_at_ms,
            )
        if task.tool_call.status == ToolCallStatus.PENDING:
            if task.tool_call.started_at_ms is not None:
                fail(
                    "waiting approval task must not have started tool_call",
                    tool_call_started_at_ms=task.tool_call.started_at_ms,
                )
            if task.started_at_ms is not None:
                fail("waiting approval task must not have started_at_ms set")
        if task.tool_call.status == ToolCallStatus.RUNNING:
            if task.started_at_ms is None:
                fail("waiting approval task must have started_at_ms when tool_call is running")
            if task.attempts <= 0:
                fail("waiting approval task must have attempts >= 1", attempts=task.attempts)
        return

    if status == TaskStatus.RUNNING:
        if task.tool_call is None:
            fail("task cannot run without tool_call")
        if task.tool_call.status != ToolCallStatus.RUNNING:
            fail(
                "task cannot run unless tool_call is running",
                tool_call_status=task.tool_call.status.value,
            )
        if task.started_at_ms is None:
            fail("task cannot run without started_at_ms")
        if task.completed_at_ms is not None:
            fail("running task must not have completed_at_ms set")
        if task.attempts <= 0:
            fail("running task must have attempts >= 1", attempts=task.attempts)
        return

    if status in {TaskStatus.SUCCEEDED, TaskStatus.FAILED}:
        if task.tool_call is None:
            fail("task cannot complete without tool_call")
        expected_tool_status = (
            ToolCallStatus.SUCCEEDED if status == TaskStatus.SUCCEEDED else ToolCallStatus.FAILED
        )
        if task.tool_call.status != expected_tool_status:
            fail(
                "task status must match tool_call status",
                tool_call_status=task.tool_call.status.value,
                expected_tool_call_status=expected_tool_status.value,
            )
        if task.started_at_ms is None:
            fail("task cannot complete without started_at_ms")
        if task.completed_at_ms is None:
            fail("task cannot complete without completed_at_ms")
        if task.attempts <= 0:
            fail("completed task must have attempts >= 1", attempts=task.attempts)
        return

    if status == TaskStatus.CANCELED:
        if task.completed_at_ms is None:
            fail("canceled task must have completed_at_ms")
        if task.tool_call is not None and task.tool_call.status not in {
            ToolCallStatus.CANCELED,
            ToolCallStatus.DENIED,
        }:
            fail(
                "canceled task must not have an active tool_call",
                tool_call_status=task.tool_call.status.value,
            )
        return

    if status == TaskStatus.ARCHIVED:
        if task.completed_at_ms is None:
            fail("archived task must have completed_at_ms")
        if task.tool_call is not None and task.tool_call.status not in {
            ToolCallStatus.SUCCEEDED,
            ToolCallStatus.FAILED,
            ToolCallStatus.CANCELED,
            ToolCallStatus.DENIED,
        }:
            fail(
                "archived task must not have an active tool_call",
                tool_call_status=task.tool_call.status.value,
            )
        return

    fail("unhandled task status")  # pragma: no cover


def _validate_tool_call_invariants(tool_call: ToolCall, *, current_state: ToolCallStatus) -> None:
    status = tool_call.status
    ctx = {"entity": "ToolCall"}

    def fail(reason: str, **extra: object) -> NoReturn:
        raise InvalidTransition(
            reason,
            current_state=current_state.value,
            requested_state=status.value,
            context={**ctx, **extra},
        )

    if status == ToolCallStatus.PENDING:
        if tool_call.started_at_ms is not None or tool_call.completed_at_ms is not None:
            fail("pending tool call cannot have started_at_ms/completed_at_ms set")
        return

    if status == ToolCallStatus.RUNNING:
        if tool_call.started_at_ms is None:
            fail("tool call cannot run without started_at_ms")
        if tool_call.completed_at_ms is not None:
            fail("running tool call must not have completed_at_ms set")
        return

    if status in {ToolCallStatus.SUCCEEDED, ToolCallStatus.FAILED}:
        if tool_call.started_at_ms is None:
            fail("tool call cannot complete without started_at_ms")
        if tool_call.completed_at_ms is None:
            fail("tool call cannot complete without completed_at_ms")
        return

    if status == ToolCallStatus.DENIED:
        if tool_call.started_at_ms is not None:
            fail("denied tool call must not have started_at_ms set")
        if tool_call.completed_at_ms is None:
            fail("denied tool call must have completed_at_ms set")
        return

    if status == ToolCallStatus.CANCELED:
        if tool_call.completed_at_ms is None:
            fail("canceled tool call must have completed_at_ms set")
        return

    fail("unhandled tool call status")  # pragma: no cover

from __future__ import annotations

import pytest

from reflexor.domain.enums import TaskStatus, ToolCallStatus
from reflexor.domain.errors import InvalidTransition
from reflexor.domain.lifecycle import (
    TASK_ALLOWED_TRANSITIONS,
    TOOL_CALL_ALLOWED_TRANSITIONS,
    _validate_task_invariants,
    _validate_tool_call_invariants,
    can_transition,
    transition,
    transition_task,
    transition_tool_call,
)
from reflexor.domain.models import Task, ToolCall

RUN_ID = "00000000-0000-4000-8000-000000000000"


def _tool_call(
    *,
    status: ToolCallStatus,
    started_at_ms: int | None = None,
    completed_at_ms: int | None = None,
) -> ToolCall:
    return ToolCall(
        tool_name="noop",
        permission_scope="tests",
        idempotency_key="k1",
        status=status,
        created_at_ms=0,
        started_at_ms=started_at_ms,
        completed_at_ms=completed_at_ms,
    )


def _task(
    *,
    status: TaskStatus,
    tool_call: ToolCall | None = None,
    attempts: int = 0,
    max_attempts: int = 1,
    started_at_ms: int | None = None,
    completed_at_ms: int | None = None,
) -> Task:
    return Task(
        run_id=RUN_ID,
        name="example",
        status=status,
        tool_call=tool_call,
        attempts=attempts,
        max_attempts=max_attempts,
        created_at_ms=0,
        started_at_ms=started_at_ms,
        completed_at_ms=completed_at_ms,
    )


def test_transition_matrices_cover_all_statuses() -> None:
    assert set(TASK_ALLOWED_TRANSITIONS) == set(TaskStatus)
    assert set(TOOL_CALL_ALLOWED_TRANSITIONS) == set(ToolCallStatus)


def test_can_transition_rejects_mixed_or_unknown_types() -> None:
    with pytest.raises(TypeError, match="can_transition expects"):
        can_transition(TaskStatus.PENDING, ToolCallStatus.PENDING)

    with pytest.raises(TypeError, match="can_transition expects"):
        can_transition("pending", "running")  # type: ignore[arg-type]


def test_transition_dispatch_rejects_wrong_target_types_and_unknown_entities() -> None:
    task = _task(status=TaskStatus.PENDING)
    tool_call = _tool_call(status=ToolCallStatus.PENDING)

    with pytest.raises(TypeError, match="Task transition target must be a TaskStatus"):
        transition(task, ToolCallStatus.PENDING)  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="ToolCall transition target must be a ToolCallStatus"):
        transition(tool_call, TaskStatus.PENDING)  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="transition expects a Task or ToolCall"):
        transition(object(), TaskStatus.PENDING)  # type: ignore[arg-type]


def test_private_pending_invariants_reject_timestamps() -> None:
    task = _task(status=TaskStatus.PENDING, started_at_ms=1)
    with pytest.raises(InvalidTransition, match="pending task cannot have started_at_ms"):
        _validate_task_invariants(task, current_state=TaskStatus.RUNNING)

    call = _tool_call(status=ToolCallStatus.PENDING, started_at_ms=1)
    with pytest.raises(InvalidTransition, match="pending tool call cannot have started_at_ms"):
        _validate_tool_call_invariants(call, current_state=ToolCallStatus.RUNNING)


def test_task_queued_requires_pending_tool_call_and_no_timestamps() -> None:
    with pytest.raises(InvalidTransition, match="queued task must have tool_call"):
        transition_task(_task(status=TaskStatus.PENDING, tool_call=None), TaskStatus.QUEUED)

    with pytest.raises(InvalidTransition, match="queued task must have pending tool_call"):
        transition_task(
            _task(
                status=TaskStatus.PENDING,
                tool_call=_tool_call(status=ToolCallStatus.RUNNING, started_at_ms=1),
            ),
            TaskStatus.QUEUED,
        )

    with pytest.raises(InvalidTransition, match="queued task cannot have started_at_ms"):
        transition_task(
            _task(
                status=TaskStatus.PENDING,
                tool_call=_tool_call(status=ToolCallStatus.PENDING),
                started_at_ms=1,
            ),
            TaskStatus.QUEUED,
        )


def test_private_running_invariants_reject_completed_and_attempts_zero() -> None:
    running_task = _task(
        status=TaskStatus.RUNNING,
        tool_call=_tool_call(status=ToolCallStatus.RUNNING, started_at_ms=1),
        attempts=0,
        started_at_ms=1,
        completed_at_ms=None,
    )
    with pytest.raises(InvalidTransition, match="attempts >= 1"):
        _validate_task_invariants(running_task, current_state=TaskStatus.PENDING)

    running_task_with_completed = _task(
        status=TaskStatus.RUNNING,
        tool_call=_tool_call(status=ToolCallStatus.RUNNING, started_at_ms=1),
        attempts=1,
        started_at_ms=1,
        completed_at_ms=2,
    )
    with pytest.raises(InvalidTransition, match="must not have completed_at_ms"):
        _validate_task_invariants(running_task_with_completed, current_state=TaskStatus.PENDING)

    running_call_with_completed = _tool_call(
        status=ToolCallStatus.RUNNING, started_at_ms=1, completed_at_ms=2
    )
    with pytest.raises(InvalidTransition, match="running tool call must not have completed_at_ms"):
        _validate_tool_call_invariants(
            running_call_with_completed, current_state=ToolCallStatus.PENDING
        )


def test_task_complete_requires_tool_call_and_attempts() -> None:
    without_call = _task(
        status=TaskStatus.RUNNING,
        tool_call=None,
        attempts=1,
        started_at_ms=1,
        completed_at_ms=2,
    )
    with pytest.raises(InvalidTransition, match="task cannot complete without tool_call"):
        transition_task(without_call, TaskStatus.SUCCEEDED)

    call = _tool_call(status=ToolCallStatus.SUCCEEDED, started_at_ms=1, completed_at_ms=2)
    attempts_zero = _task(
        status=TaskStatus.RUNNING,
        tool_call=call,
        attempts=0,
        started_at_ms=1,
        completed_at_ms=2,
    )
    with pytest.raises(InvalidTransition, match="completed task must have attempts"):
        transition_task(attempts_zero, TaskStatus.SUCCEEDED)


def test_task_waiting_approval_invariants() -> None:
    with pytest.raises(InvalidTransition, match="waiting approval task must have tool_call"):
        _validate_task_invariants(
            _task(status=TaskStatus.WAITING_APPROVAL, tool_call=None),
            current_state=TaskStatus.QUEUED,
        )

    with pytest.raises(
        InvalidTransition, match="waiting approval task must not have completed_at_ms"
    ):
        _validate_task_invariants(
            _task(
                status=TaskStatus.WAITING_APPROVAL,
                tool_call=_tool_call(status=ToolCallStatus.PENDING),
                completed_at_ms=1,
            ),
            current_state=TaskStatus.QUEUED,
        )

    with pytest.raises(
        InvalidTransition, match="waiting approval task must not have started tool_call"
    ):
        _validate_task_invariants(
            _task(
                status=TaskStatus.WAITING_APPROVAL,
                tool_call=_tool_call(status=ToolCallStatus.PENDING, started_at_ms=1),
            ),
            current_state=TaskStatus.QUEUED,
        )

    with pytest.raises(InvalidTransition, match="waiting approval task must have attempts >= 1"):
        _validate_task_invariants(
            _task(
                status=TaskStatus.WAITING_APPROVAL,
                tool_call=_tool_call(status=ToolCallStatus.RUNNING, started_at_ms=1),
                attempts=0,
                started_at_ms=1,
            ),
            current_state=TaskStatus.RUNNING,
        )


def test_tool_call_complete_requires_started_and_cancel_requires_completed() -> None:
    missing_started = _tool_call(
        status=ToolCallStatus.RUNNING, started_at_ms=None, completed_at_ms=2
    )
    with pytest.raises(InvalidTransition, match="tool call cannot complete without started_at_ms"):
        transition_tool_call(missing_started, ToolCallStatus.SUCCEEDED)

    missing_completed = _tool_call(
        status=ToolCallStatus.RUNNING, started_at_ms=1, completed_at_ms=None
    )
    with pytest.raises(InvalidTransition, match="canceled tool call must have completed_at_ms"):
        transition_tool_call(missing_completed, ToolCallStatus.CANCELED)

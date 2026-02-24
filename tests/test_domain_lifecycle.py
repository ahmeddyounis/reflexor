from __future__ import annotations

import pytest

from reflexor.domain.enums import TaskStatus, ToolCallStatus
from reflexor.domain.errors import InvalidTransition
from reflexor.domain.lifecycle import (
    can_transition,
    transition,
    transition_task,
    transition_tool_call,
)
from reflexor.domain.models import Task, ToolCall

RUN_ID = "00000000-0000-4000-8000-000000000000"

EXPECTED_TASK_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.PENDING: {TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.CANCELED},
    TaskStatus.QUEUED: {TaskStatus.WAITING_APPROVAL, TaskStatus.RUNNING, TaskStatus.CANCELED},
    TaskStatus.WAITING_APPROVAL: {TaskStatus.QUEUED, TaskStatus.CANCELED},
    TaskStatus.RUNNING: {
        TaskStatus.WAITING_APPROVAL,
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.CANCELED,
    },
    TaskStatus.FAILED: {TaskStatus.RUNNING, TaskStatus.CANCELED},
    TaskStatus.SUCCEEDED: set(),
    TaskStatus.CANCELED: set(),
}

EXPECTED_TOOL_CALL_TRANSITIONS: dict[ToolCallStatus, set[ToolCallStatus]] = {
    ToolCallStatus.PENDING: {
        ToolCallStatus.RUNNING,
        ToolCallStatus.DENIED,
        ToolCallStatus.CANCELED,
    },
    ToolCallStatus.RUNNING: {
        ToolCallStatus.SUCCEEDED,
        ToolCallStatus.FAILED,
        ToolCallStatus.CANCELED,
    },
    ToolCallStatus.FAILED: {ToolCallStatus.RUNNING, ToolCallStatus.CANCELED},
    ToolCallStatus.SUCCEEDED: set(),
    ToolCallStatus.DENIED: set(),
    ToolCallStatus.CANCELED: set(),
}


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


def test_can_transition_task_matrix() -> None:
    for current in TaskStatus:
        for target in TaskStatus:
            assert can_transition(current, target) is (target in EXPECTED_TASK_TRANSITIONS[current])


def test_can_transition_tool_call_matrix() -> None:
    for current in ToolCallStatus:
        for target in ToolCallStatus:
            assert can_transition(current, target) is (
                target in EXPECTED_TOOL_CALL_TRANSITIONS[current]
            )


def test_task_transition_invalid_edges_raise() -> None:
    for current in TaskStatus:
        task = _task(status=current)
        for target in TaskStatus:
            if target in EXPECTED_TASK_TRANSITIONS[current]:
                continue
            with pytest.raises(InvalidTransition):
                transition_task(task, target)


def test_tool_call_transition_invalid_edges_raise() -> None:
    for current in ToolCallStatus:
        tool_call = _tool_call(status=current)
        for target in ToolCallStatus:
            if target in EXPECTED_TOOL_CALL_TRANSITIONS[current]:
                continue
            with pytest.raises(InvalidTransition):
                transition_tool_call(tool_call, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (TaskStatus.PENDING, TaskStatus.QUEUED),
        (TaskStatus.PENDING, TaskStatus.RUNNING),
        (TaskStatus.PENDING, TaskStatus.CANCELED),
        (TaskStatus.QUEUED, TaskStatus.WAITING_APPROVAL),
        (TaskStatus.QUEUED, TaskStatus.RUNNING),
        (TaskStatus.QUEUED, TaskStatus.CANCELED),
        (TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL),
        (TaskStatus.RUNNING, TaskStatus.SUCCEEDED),
        (TaskStatus.RUNNING, TaskStatus.FAILED),
        (TaskStatus.RUNNING, TaskStatus.CANCELED),
        (TaskStatus.WAITING_APPROVAL, TaskStatus.QUEUED),
        (TaskStatus.WAITING_APPROVAL, TaskStatus.CANCELED),
        (TaskStatus.FAILED, TaskStatus.RUNNING),
        (TaskStatus.FAILED, TaskStatus.CANCELED),
    ],
)
def test_task_transition_all_allowed_edges(current: TaskStatus, target: TaskStatus) -> None:
    task = _task_for_transition(current=current, target=target)
    transitioned = transition_task(task, target)
    assert transitioned.status == target


def _task_for_transition(*, current: TaskStatus, target: TaskStatus) -> Task:
    if target == TaskStatus.QUEUED:
        tool_call = _tool_call(status=ToolCallStatus.PENDING)
        return _task(
            status=current,
            tool_call=tool_call,
            attempts=0,
            max_attempts=1,
        )

    if target == TaskStatus.WAITING_APPROVAL:
        if current == TaskStatus.RUNNING:
            tool_call = _tool_call(status=ToolCallStatus.RUNNING, started_at_ms=1)
            return _task(
                status=current,
                tool_call=tool_call,
                attempts=1,
                max_attempts=1,
                started_at_ms=1,
                completed_at_ms=None,
            )
        tool_call = _tool_call(status=ToolCallStatus.PENDING)
        return _task(
            status=current,
            tool_call=tool_call,
            attempts=0,
            max_attempts=1,
        )

    if target == TaskStatus.RUNNING:
        tool_call = _tool_call(status=ToolCallStatus.RUNNING, started_at_ms=1)
        if current == TaskStatus.FAILED:
            return _task(
                status=current,
                tool_call=tool_call,
                attempts=1,
                max_attempts=2,
                started_at_ms=1,
                completed_at_ms=2,
            )
        return _task(
            status=current,
            tool_call=tool_call,
            attempts=0,
            max_attempts=1,
            started_at_ms=1,
            completed_at_ms=2,
        )

    if target == TaskStatus.SUCCEEDED:
        tool_call = _tool_call(status=ToolCallStatus.SUCCEEDED, started_at_ms=1, completed_at_ms=2)
        return _task(
            status=current,
            tool_call=tool_call,
            attempts=1,
            max_attempts=1,
            started_at_ms=1,
            completed_at_ms=2,
        )

    if target == TaskStatus.FAILED:
        tool_call = _tool_call(status=ToolCallStatus.FAILED, started_at_ms=1, completed_at_ms=2)
        return _task(
            status=current,
            tool_call=tool_call,
            attempts=1,
            max_attempts=1,
            started_at_ms=1,
            completed_at_ms=2,
        )

    if target == TaskStatus.CANCELED:
        if current == TaskStatus.RUNNING:
            tool_call = _tool_call(
                status=ToolCallStatus.CANCELED, started_at_ms=1, completed_at_ms=2
            )
            return _task(
                status=current,
                tool_call=tool_call,
                attempts=1,
                started_at_ms=1,
                completed_at_ms=2,
            )
        if current == TaskStatus.FAILED:
            tool_call = _tool_call(status=ToolCallStatus.DENIED, completed_at_ms=2)
            return _task(
                status=current,
                tool_call=tool_call,
                attempts=1,
                started_at_ms=1,
                completed_at_ms=2,
            )
        return _task(status=current, completed_at_ms=1)

    raise AssertionError(
        f"unhandled task transition {current.value} → {target.value}"
    )  # pragma: no cover


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (ToolCallStatus.PENDING, ToolCallStatus.RUNNING),
        (ToolCallStatus.PENDING, ToolCallStatus.DENIED),
        (ToolCallStatus.PENDING, ToolCallStatus.CANCELED),
        (ToolCallStatus.RUNNING, ToolCallStatus.SUCCEEDED),
        (ToolCallStatus.RUNNING, ToolCallStatus.FAILED),
        (ToolCallStatus.RUNNING, ToolCallStatus.CANCELED),
        (ToolCallStatus.FAILED, ToolCallStatus.RUNNING),
        (ToolCallStatus.FAILED, ToolCallStatus.CANCELED),
    ],
)
def test_tool_call_transition_all_allowed_edges(
    current: ToolCallStatus, target: ToolCallStatus
) -> None:
    tool_call = _tool_call_for_transition(current=current, target=target)
    transitioned = transition_tool_call(tool_call, target)
    assert transitioned.status == target


def _tool_call_for_transition(*, current: ToolCallStatus, target: ToolCallStatus) -> ToolCall:
    if target == ToolCallStatus.RUNNING:
        return _tool_call(status=current, started_at_ms=1, completed_at_ms=2)

    if target == ToolCallStatus.DENIED:
        return _tool_call(status=current, completed_at_ms=1)

    if target == ToolCallStatus.CANCELED:
        return _tool_call(status=current, completed_at_ms=1)

    if target in {ToolCallStatus.SUCCEEDED, ToolCallStatus.FAILED}:
        return _tool_call(status=current, started_at_ms=1, completed_at_ms=2)

    raise AssertionError(  # pragma: no cover
        f"unhandled tool call transition {current.value} → {target.value}"
    )


def test_task_running_invariants() -> None:
    tool_call = _tool_call(status=ToolCallStatus.RUNNING, started_at_ms=1)

    with pytest.raises(InvalidTransition, match="task cannot run without tool_call"):
        transition_task(_task(status=TaskStatus.PENDING, started_at_ms=1), TaskStatus.RUNNING)

    with pytest.raises(InvalidTransition, match="task cannot run without started_at_ms"):
        transition_task(_task(status=TaskStatus.PENDING, tool_call=tool_call), TaskStatus.RUNNING)

    pending_with_wrong_tool_status = _task(
        status=TaskStatus.PENDING,
        tool_call=_tool_call(status=ToolCallStatus.PENDING),
        started_at_ms=1,
    )
    with pytest.raises(InvalidTransition, match="tool_call is running"):
        transition_task(pending_with_wrong_tool_status, TaskStatus.RUNNING)


def test_task_completion_invariants() -> None:
    tool_call = _tool_call(status=ToolCallStatus.SUCCEEDED, started_at_ms=1, completed_at_ms=2)

    running_without_started = _task(
        status=TaskStatus.RUNNING,
        tool_call=tool_call,
        attempts=1,
        completed_at_ms=2,
    )
    with pytest.raises(InvalidTransition, match="task cannot complete without started_at_ms"):
        transition_task(running_without_started, TaskStatus.SUCCEEDED)

    running_without_completed = _task(
        status=TaskStatus.RUNNING,
        tool_call=tool_call,
        attempts=1,
        started_at_ms=1,
    )
    with pytest.raises(InvalidTransition, match="task cannot complete without completed_at_ms"):
        transition_task(running_without_completed, TaskStatus.SUCCEEDED)

    running_with_mismatched_tool_status = _task(
        status=TaskStatus.RUNNING,
        tool_call=_tool_call(status=ToolCallStatus.FAILED, started_at_ms=1, completed_at_ms=2),
        attempts=1,
        started_at_ms=1,
        completed_at_ms=2,
    )
    with pytest.raises(InvalidTransition, match="task status must match tool_call status"):
        transition_task(running_with_mismatched_tool_status, TaskStatus.SUCCEEDED)


def test_task_retry_respects_max_attempts() -> None:
    tool_call = _tool_call(status=ToolCallStatus.RUNNING, started_at_ms=1)

    task = _task(
        status=TaskStatus.FAILED,
        tool_call=tool_call,
        attempts=1,
        max_attempts=1,
        started_at_ms=1,
        completed_at_ms=2,
    )
    with pytest.raises(InvalidTransition, match="max_attempts reached"):
        transition_task(task, TaskStatus.RUNNING)


def test_task_running_transition_increments_attempts_and_clears_completed_at() -> None:
    tool_call = _tool_call(status=ToolCallStatus.RUNNING, started_at_ms=1)

    task = _task(
        status=TaskStatus.FAILED,
        tool_call=tool_call,
        attempts=1,
        max_attempts=2,
        started_at_ms=1,
        completed_at_ms=2,
    )
    transitioned = transition_task(task, TaskStatus.RUNNING)
    assert transitioned.attempts == 2
    assert transitioned.completed_at_ms is None


def test_task_cancel_requires_completion_and_no_active_tool_call() -> None:
    tool_call = _tool_call(status=ToolCallStatus.RUNNING, started_at_ms=1)

    with pytest.raises(InvalidTransition, match="canceled task must have completed_at_ms"):
        transition_task(_task(status=TaskStatus.PENDING), TaskStatus.CANCELED)

    task = _task(
        status=TaskStatus.RUNNING,
        tool_call=tool_call,
        attempts=1,
        started_at_ms=1,
        completed_at_ms=2,
    )
    with pytest.raises(InvalidTransition, match="must not have an active tool_call"):
        transition_task(task, TaskStatus.CANCELED)


def test_tool_call_invariants() -> None:
    with pytest.raises(InvalidTransition, match="tool call cannot run without started_at_ms"):
        transition_tool_call(_tool_call(status=ToolCallStatus.PENDING), ToolCallStatus.RUNNING)

    with pytest.raises(
        InvalidTransition, match="tool call cannot complete without completed_at_ms"
    ):
        transition_tool_call(
            _tool_call(status=ToolCallStatus.RUNNING, started_at_ms=1), ToolCallStatus.SUCCEEDED
        )

    with pytest.raises(InvalidTransition, match="denied tool call must not have started_at_ms"):
        transition_tool_call(
            _tool_call(status=ToolCallStatus.PENDING, started_at_ms=1, completed_at_ms=2),
            ToolCallStatus.DENIED,
        )

    with pytest.raises(InvalidTransition, match="denied tool call must have completed_at_ms"):
        transition_tool_call(_tool_call(status=ToolCallStatus.PENDING), ToolCallStatus.DENIED)


def test_transition_dispatcher_handles_task_and_tool_call() -> None:
    tool_call = _tool_call(status=ToolCallStatus.RUNNING, started_at_ms=1)
    task = _task(
        status=TaskStatus.PENDING,
        tool_call=tool_call,
        started_at_ms=1,
        completed_at_ms=2,
    )

    transitioned_task = transition(task, TaskStatus.RUNNING)
    assert isinstance(transitioned_task, Task)
    assert transitioned_task.status == TaskStatus.RUNNING

    transitioned_call = transition(
        _tool_call(status=ToolCallStatus.PENDING, started_at_ms=1), ToolCallStatus.RUNNING
    )
    assert isinstance(transitioned_call, ToolCall)
    assert transitioned_call.status == ToolCallStatus.RUNNING

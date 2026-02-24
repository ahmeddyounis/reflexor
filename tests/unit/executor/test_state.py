from __future__ import annotations

import pytest

from reflexor.domain.enums import TaskStatus, ToolCallStatus
from reflexor.domain.errors import InvalidTransition
from reflexor.domain.models import Task, ToolCall
from reflexor.executor.state import (
    complete_denied,
    complete_failed,
    complete_succeeded,
    mark_waiting_approval,
    start_execution,
)


def _tool_call() -> ToolCall:
    return ToolCall(
        tool_call_id="11111111-1111-4111-8111-111111111111",
        tool_name="tests.mock",
        args={"x": 1},
        permission_scope="debug.echo",
        idempotency_key="k",
        status=ToolCallStatus.PENDING,
        created_at_ms=0,
    )


def _task(tool_call: ToolCall) -> Task:
    return Task(
        task_id="22222222-2222-4222-8222-222222222222",
        run_id="33333333-3333-4333-8333-333333333333",
        name="t",
        status=TaskStatus.QUEUED,
        tool_call=tool_call,
        max_attempts=3,
        timeout_s=60,
        created_at_ms=0,
    )


def test_start_and_complete_success_sets_timestamps_and_attempts() -> None:
    tool_call = _tool_call()
    task = _task(tool_call)

    running = start_execution(task=task, tool_call=tool_call, now_ms=1_000)
    assert running.tool_call.status == ToolCallStatus.RUNNING
    assert running.tool_call.started_at_ms == 1_000
    assert running.tool_call.completed_at_ms is None

    assert running.task.status == TaskStatus.RUNNING
    assert running.task.started_at_ms == 1_000
    assert running.task.completed_at_ms is None
    assert running.task.attempts == 1

    succeeded = complete_succeeded(task=running.task, tool_call=running.tool_call, now_ms=1_001)
    assert succeeded.tool_call.status == ToolCallStatus.SUCCEEDED
    assert succeeded.tool_call.completed_at_ms == 1_001
    assert succeeded.task.status == TaskStatus.SUCCEEDED
    assert succeeded.task.completed_at_ms == 1_001
    assert succeeded.task.attempts == 1


def test_complete_success_from_non_running_raises() -> None:
    tool_call = _tool_call()
    task = _task(tool_call)

    with pytest.raises(InvalidTransition):
        complete_succeeded(task=task, tool_call=tool_call, now_ms=1_000)


def test_complete_failed_from_non_running_raises() -> None:
    tool_call = _tool_call()
    task = _task(tool_call)

    with pytest.raises(InvalidTransition):
        complete_failed(task=task, tool_call=tool_call, now_ms=1_000)


def test_mark_waiting_approval_does_not_modify_tool_call() -> None:
    tool_call = _tool_call()
    task = _task(tool_call)

    waiting = mark_waiting_approval(task=task, tool_call=tool_call)
    assert waiting.task.status == TaskStatus.WAITING_APPROVAL
    assert waiting.tool_call.status == ToolCallStatus.PENDING


def test_complete_denied_sets_completed_and_cancels_task() -> None:
    tool_call = _tool_call()
    task = _task(tool_call)

    denied = complete_denied(task=task, tool_call=tool_call, now_ms=1_000)
    assert denied.tool_call.status == ToolCallStatus.DENIED
    assert denied.tool_call.started_at_ms is None
    assert denied.tool_call.completed_at_ms == 1_000
    assert denied.task.status == TaskStatus.CANCELED
    assert denied.task.completed_at_ms == 1_000

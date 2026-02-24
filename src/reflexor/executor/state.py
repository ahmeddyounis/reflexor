"""Executor state transition helpers.

This module applies domain lifecycle transitions (and required timestamp/attempt mutations) before
persisting executor updates. It is intentionally pure and deterministic: callers provide `now_ms`
explicitly.

Clean Architecture:
- Allowed dependencies: `reflexor.domain` only.
- Forbidden: DB/queue/tool imports.
"""

from __future__ import annotations

from dataclasses import dataclass

from reflexor.domain.enums import TaskStatus, ToolCallStatus
from reflexor.domain.lifecycle import transition_task, transition_tool_call
from reflexor.domain.models import Task, ToolCall


@dataclass(frozen=True, slots=True)
class ExecutionState:
    task: Task
    tool_call: ToolCall


def start_execution(*, task: Task, tool_call: ToolCall, now_ms: int) -> ExecutionState:
    """Transition (task, tool_call) to RUNNING and set started_at_ms + attempts."""

    prepared_tool_call = tool_call.model_copy(
        update={"started_at_ms": int(now_ms), "completed_at_ms": None}
    )
    running_tool_call = transition_tool_call(prepared_tool_call, ToolCallStatus.RUNNING)

    prepared_task = task.model_copy(
        update={
            "tool_call": running_tool_call,
            "started_at_ms": int(now_ms),
            "completed_at_ms": None,
        }
    )
    running_task = transition_task(prepared_task, TaskStatus.RUNNING)

    return ExecutionState(task=running_task, tool_call=running_tool_call)


def mark_waiting_approval(*, task: Task, tool_call: ToolCall) -> ExecutionState:
    """Transition task to WAITING_APPROVAL without changing tool_call."""

    prepared_task = task.model_copy(update={"tool_call": tool_call})
    waiting_task = transition_task(prepared_task, TaskStatus.WAITING_APPROVAL)
    return ExecutionState(task=waiting_task, tool_call=tool_call)


def complete_succeeded(*, task: Task, tool_call: ToolCall, now_ms: int) -> ExecutionState:
    """Transition (task, tool_call) to SUCCEEDED and set completed_at_ms."""

    prepared_tool_call = tool_call.model_copy(update={"completed_at_ms": int(now_ms)})
    succeeded_tool_call = transition_tool_call(prepared_tool_call, ToolCallStatus.SUCCEEDED)

    prepared_task = task.model_copy(
        update={"tool_call": succeeded_tool_call, "completed_at_ms": int(now_ms)}
    )
    succeeded_task = transition_task(prepared_task, TaskStatus.SUCCEEDED)
    return ExecutionState(task=succeeded_task, tool_call=succeeded_tool_call)


def complete_failed(*, task: Task, tool_call: ToolCall, now_ms: int) -> ExecutionState:
    """Transition (task, tool_call) to FAILED and set completed_at_ms."""

    prepared_tool_call = tool_call.model_copy(update={"completed_at_ms": int(now_ms)})
    failed_tool_call = transition_tool_call(prepared_tool_call, ToolCallStatus.FAILED)

    prepared_task = task.model_copy(
        update={"tool_call": failed_tool_call, "completed_at_ms": int(now_ms)}
    )
    failed_task = transition_task(prepared_task, TaskStatus.FAILED)
    return ExecutionState(task=failed_task, tool_call=failed_tool_call)


def complete_denied(*, task: Task, tool_call: ToolCall, now_ms: int) -> ExecutionState:
    """Transition tool_call to DENIED and task to CANCELED (no started_at_ms)."""

    prepared_tool_call = tool_call.model_copy(update={"completed_at_ms": int(now_ms)})
    denied_tool_call = transition_tool_call(prepared_tool_call, ToolCallStatus.DENIED)

    prepared_task = task.model_copy(
        update={"tool_call": denied_tool_call, "completed_at_ms": int(now_ms)}
    )
    canceled_task = transition_task(prepared_task, TaskStatus.CANCELED)
    return ExecutionState(task=canceled_task, tool_call=denied_tool_call)


def complete_canceled(*, task: Task, tool_call: ToolCall, now_ms: int) -> ExecutionState:
    """Transition tool_call+task to CANCELED and set completed_at_ms."""

    prepared_tool_call = tool_call.model_copy(update={"completed_at_ms": int(now_ms)})
    canceled_tool_call = transition_tool_call(prepared_tool_call, ToolCallStatus.CANCELED)

    prepared_task = task.model_copy(
        update={"tool_call": canceled_tool_call, "completed_at_ms": int(now_ms)}
    )
    canceled_task = transition_task(prepared_task, TaskStatus.CANCELED)
    return ExecutionState(task=canceled_task, tool_call=canceled_tool_call)


__all__ = [
    "ExecutionState",
    "complete_canceled",
    "complete_denied",
    "complete_failed",
    "complete_succeeded",
    "mark_waiting_approval",
    "start_execution",
]

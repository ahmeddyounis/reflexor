from __future__ import annotations

from reflexor.domain.enums import TaskStatus, ToolCallStatus
from reflexor.domain.execution_state import (
    ExecutionState,
    complete_canceled,
    complete_denied,
    complete_failed,
    complete_succeeded,
    mark_waiting_approval,
    start_execution,
)
from reflexor.domain.models import Task, ToolCall
from reflexor.executor.service.types import ExecutionDisposition


def apply_state_transition(
    *,
    task: Task,
    tool_call: ToolCall,
    disposition: ExecutionDisposition,
    now_ms: int,
) -> ExecutionState:
    if disposition == ExecutionDisposition.WAITING_APPROVAL:
        return mark_waiting_approval(task=task, tool_call=tool_call)

    if disposition == ExecutionDisposition.DENIED:
        if tool_call.status == ToolCallStatus.PENDING:
            return complete_denied(task=task, tool_call=tool_call, now_ms=now_ms)
        return complete_canceled(task=task, tool_call=tool_call, now_ms=now_ms)

    if disposition == ExecutionDisposition.CANCELED:
        return complete_canceled(task=task, tool_call=tool_call, now_ms=now_ms)

    if disposition == ExecutionDisposition.SUCCEEDED:
        if task.status != TaskStatus.RUNNING or tool_call.status != ToolCallStatus.RUNNING:
            started = start_execution(task=task, tool_call=tool_call, now_ms=now_ms)
            task = started.task
            tool_call = started.tool_call
        return complete_succeeded(task=task, tool_call=tool_call, now_ms=now_ms)

    if disposition in {
        ExecutionDisposition.FAILED_TRANSIENT,
        ExecutionDisposition.FAILED_PERMANENT,
    }:
        if task.status != TaskStatus.RUNNING or tool_call.status != ToolCallStatus.RUNNING:
            started = start_execution(task=task, tool_call=tool_call, now_ms=now_ms)
            task = started.task
            tool_call = started.tool_call
        return complete_failed(task=task, tool_call=tool_call, now_ms=now_ms)

    raise ValueError(f"unhandled disposition: {disposition}")

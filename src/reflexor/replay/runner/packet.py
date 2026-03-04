from __future__ import annotations

from collections.abc import Mapping
from uuid import uuid4

from pydantic import ValidationError

from reflexor.domain.enums import TaskStatus, ToolCallStatus
from reflexor.domain.models import Task, ToolCall
from reflexor.domain.models_run_packet import RunPacket
from reflexor.tools.sdk import ToolResult


def _build_replay_tasks(
    packet: RunPacket, *, replay_run_id: str
) -> tuple[list[Task], dict[str, str]]:
    task_id_map: dict[str, str] = {task.task_id: str(uuid4()) for task in packet.tasks}
    tool_call_id_map: dict[str, str] = {}

    tasks: list[Task] = []
    for task in packet.tasks:
        tool_call = task.tool_call
        replay_tool_call: ToolCall | None = None
        if tool_call is not None:
            new_tool_call_id = str(uuid4())
            tool_call_id_map[tool_call.tool_call_id] = new_tool_call_id
            replay_tool_call = ToolCall(
                tool_call_id=new_tool_call_id,
                tool_name=tool_call.tool_name,
                args=dict(tool_call.args),
                permission_scope=tool_call.permission_scope,
                idempotency_key=tool_call.idempotency_key,
                status=ToolCallStatus.PENDING,
                created_at_ms=tool_call.created_at_ms,
                started_at_ms=None,
                completed_at_ms=None,
                result_ref=None,
            )

        new_task_id = task_id_map[task.task_id]
        depends_on = [task_id_map.get(dep, dep) for dep in task.depends_on]

        metadata = dict(task.metadata)
        metadata.setdefault("replay", {})
        if isinstance(metadata["replay"], dict):
            metadata["replay"].update(
                {
                    "original_task_id": task.task_id,
                    "original_run_id": packet.run_id,
                    "original_tool_call_id": None if tool_call is None else tool_call.tool_call_id,
                }
            )

        tasks.append(
            Task(
                task_id=new_task_id,
                run_id=replay_run_id,
                name=task.name,
                status=TaskStatus.PENDING,
                tool_call=replay_tool_call,
                attempts=0,
                max_attempts=task.max_attempts,
                timeout_s=task.timeout_s,
                depends_on=depends_on,
                created_at_ms=task.created_at_ms,
                started_at_ms=None,
                completed_at_ms=None,
                labels=list(task.labels),
                metadata=metadata,
            )
        )

    return tasks, tool_call_id_map


def _extract_recorded_tool_results(packet: RunPacket) -> dict[str, ToolResult]:
    results: dict[str, ToolResult] = {}
    for entry in packet.tool_results:
        tool_call_id = entry.get("tool_call_id")
        if not isinstance(tool_call_id, str) or not tool_call_id.strip():
            continue

        candidate = entry.get("result_summary")
        if not isinstance(candidate, Mapping):
            candidate = entry.get("result")
        if not isinstance(candidate, Mapping):
            continue

        try:
            result = ToolResult.model_validate(candidate)
        except ValidationError:
            continue

        results[tool_call_id] = result
    return results

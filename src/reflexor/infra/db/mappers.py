from __future__ import annotations

from collections.abc import Mapping

from reflexor.domain.models import Approval, Task, ToolCall
from reflexor.domain.models_event import Event
from reflexor.domain.models_run_packet import RunPacket
from reflexor.infra.db.models import (
    ApprovalRow,
    EventRow,
    RunPacketRow,
    TaskRow,
    ToolCallRow,
)


def event_to_row_dict(event: Event) -> dict[str, object]:
    dumped = event.model_dump(mode="json")
    return {
        "event_id": dumped["event_id"],
        "type": dumped["type"],
        "source": dumped["source"],
        "received_at_ms": dumped["received_at_ms"],
        "payload": dumped["payload"],
        "dedupe_key": dumped.get("dedupe_key"),
    }


def event_from_row_dict(row: Mapping[str, object]) -> Event:
    return Event.model_validate(
        {
            "event_id": row["event_id"],
            "type": row["type"],
            "source": row["source"],
            "received_at_ms": row["received_at_ms"],
            "payload": row["payload"],
            "dedupe_key": row.get("dedupe_key"),
        }
    )


def event_to_orm(event: Event) -> EventRow:
    return EventRow(**event_to_row_dict(event))


def event_from_orm(row: EventRow) -> Event:
    return event_from_row_dict(
        {
            "event_id": row.event_id,
            "type": row.type,
            "source": row.source,
            "received_at_ms": row.received_at_ms,
            "payload": row.payload,
            "dedupe_key": row.dedupe_key,
        }
    )


def tool_call_to_row_dict(tool_call: ToolCall) -> dict[str, object]:
    dumped = tool_call.model_dump(mode="json")
    return {
        "tool_call_id": dumped["tool_call_id"],
        "tool_name": dumped["tool_name"],
        "args": dumped["args"],
        "permission_scope": dumped["permission_scope"],
        "idempotency_key": dumped["idempotency_key"],
        "status": dumped["status"],
        "created_at_ms": dumped["created_at_ms"],
        "started_at_ms": dumped.get("started_at_ms"),
        "completed_at_ms": dumped.get("completed_at_ms"),
        "result_ref": dumped.get("result_ref"),
    }


def tool_call_from_row_dict(row: Mapping[str, object]) -> ToolCall:
    return ToolCall.model_validate(
        {
            "tool_call_id": row["tool_call_id"],
            "tool_name": row["tool_name"],
            "args": row.get("args", {}),
            "permission_scope": row["permission_scope"],
            "idempotency_key": row["idempotency_key"],
            "status": row.get("status", "pending"),
            "created_at_ms": row["created_at_ms"],
            "started_at_ms": row.get("started_at_ms"),
            "completed_at_ms": row.get("completed_at_ms"),
            "result_ref": row.get("result_ref"),
        }
    )


def tool_call_to_orm(tool_call: ToolCall) -> ToolCallRow:
    return ToolCallRow(**tool_call_to_row_dict(tool_call))


def tool_call_from_orm(row: ToolCallRow) -> ToolCall:
    return tool_call_from_row_dict(
        {
            "tool_call_id": row.tool_call_id,
            "tool_name": row.tool_name,
            "args": row.args,
            "permission_scope": row.permission_scope,
            "idempotency_key": row.idempotency_key,
            "status": row.status,
            "created_at_ms": row.created_at_ms,
            "started_at_ms": row.started_at_ms,
            "completed_at_ms": row.completed_at_ms,
            "result_ref": row.result_ref,
        }
    )


def task_to_row_dict(task: Task) -> dict[str, object]:
    dumped = task.model_dump(mode="json")
    tool_call_dumped = dumped.get("tool_call")
    tool_call_id = None
    if isinstance(tool_call_dumped, dict):
        tool_call_id = tool_call_dumped.get("tool_call_id")

    return {
        "task_id": dumped["task_id"],
        "run_id": dumped["run_id"],
        "name": dumped["name"],
        "status": dumped["status"],
        "tool_call_id": tool_call_id,
        "attempts": dumped["attempts"],
        "max_attempts": dumped["max_attempts"],
        "timeout_s": dumped["timeout_s"],
        "depends_on": dumped.get("depends_on", []),
        "created_at_ms": dumped["created_at_ms"],
        "started_at_ms": dumped.get("started_at_ms"),
        "completed_at_ms": dumped.get("completed_at_ms"),
        "labels": dumped.get("labels", []),
        "metadata_json": dumped.get("metadata", {}),
    }


def task_from_row_dict(
    row: Mapping[str, object],
    *,
    tool_call_row: Mapping[str, object] | None = None,
) -> Task:
    tool_call = None if tool_call_row is None else tool_call_from_row_dict(tool_call_row)
    return Task.model_validate(
        {
            "task_id": row["task_id"],
            "run_id": row["run_id"],
            "name": row["name"],
            "status": row.get("status", "pending"),
            "tool_call": None if tool_call is None else tool_call.model_dump(mode="json"),
            "attempts": row.get("attempts", 0),
            "max_attempts": row.get("max_attempts", 1),
            "timeout_s": row.get("timeout_s", 60),
            "depends_on": row.get("depends_on", []),
            "created_at_ms": row["created_at_ms"],
            "started_at_ms": row.get("started_at_ms"),
            "completed_at_ms": row.get("completed_at_ms"),
            "labels": row.get("labels", []),
            "metadata": row.get("metadata_json", {}),
        }
    )


def task_to_orm(task: Task) -> TaskRow:
    return TaskRow(**task_to_row_dict(task))


def task_from_orm(row: TaskRow, *, tool_call: ToolCallRow | None = None) -> Task:
    tool_call_row = (
        None if tool_call is None else tool_call_to_row_dict(tool_call_from_orm(tool_call))
    )
    return task_from_row_dict(
        {
            "task_id": row.task_id,
            "run_id": row.run_id,
            "name": row.name,
            "status": row.status,
            "tool_call_id": row.tool_call_id,
            "attempts": row.attempts,
            "max_attempts": row.max_attempts,
            "timeout_s": row.timeout_s,
            "depends_on": row.depends_on,
            "created_at_ms": row.created_at_ms,
            "started_at_ms": row.started_at_ms,
            "completed_at_ms": row.completed_at_ms,
            "labels": row.labels,
            "metadata_json": row.metadata_json,
        },
        tool_call_row=tool_call_row,
    )


def approval_to_row_dict(approval: Approval) -> dict[str, object]:
    dumped = approval.model_dump(mode="json")
    return {
        "approval_id": dumped["approval_id"],
        "run_id": dumped["run_id"],
        "task_id": dumped["task_id"],
        "tool_call_id": dumped["tool_call_id"],
        "status": dumped["status"],
        "created_at_ms": dumped["created_at_ms"],
        "decided_at_ms": dumped.get("decided_at_ms"),
        "decided_by": dumped.get("decided_by"),
        "payload_hash": dumped.get("payload_hash"),
        "preview": dumped.get("preview"),
    }


def approval_from_row_dict(row: Mapping[str, object]) -> Approval:
    return Approval.model_validate(
        {
            "approval_id": row["approval_id"],
            "run_id": row["run_id"],
            "task_id": row["task_id"],
            "tool_call_id": row["tool_call_id"],
            "status": row.get("status", "pending"),
            "created_at_ms": row["created_at_ms"],
            "decided_at_ms": row.get("decided_at_ms"),
            "decided_by": row.get("decided_by"),
            "payload_hash": row.get("payload_hash"),
            "preview": row.get("preview"),
        }
    )


def approval_to_orm(approval: Approval) -> ApprovalRow:
    return ApprovalRow(**approval_to_row_dict(approval))


def approval_from_orm(row: ApprovalRow) -> Approval:
    return approval_from_row_dict(
        {
            "approval_id": row.approval_id,
            "run_id": row.run_id,
            "task_id": row.task_id,
            "tool_call_id": row.tool_call_id,
            "status": row.status,
            "created_at_ms": row.created_at_ms,
            "decided_at_ms": row.decided_at_ms,
            "decided_by": row.decided_by,
            "payload_hash": row.payload_hash,
            "preview": row.preview,
        }
    )


def run_packet_to_row_dict(packet: RunPacket) -> dict[str, object]:
    dumped = packet.model_dump(mode="json")
    created_at_ms = int(dumped.get("created_at_ms") or 0)
    return {
        "run_id": dumped["run_id"],
        "created_at_ms": created_at_ms,
        "packet": dumped,
    }


def run_packet_from_row_dict(row: Mapping[str, object]) -> RunPacket:
    packet = row["packet"]
    if not isinstance(packet, dict):
        raise TypeError("row['packet'] must be a dict")
    return RunPacket.model_validate(packet)


def run_packet_to_orm(packet: RunPacket) -> RunPacketRow:
    return RunPacketRow(**run_packet_to_row_dict(packet))


def run_packet_from_orm(row: RunPacketRow) -> RunPacket:
    return run_packet_from_row_dict(
        {
            "run_id": row.run_id,
            "created_at_ms": row.created_at_ms,
            "packet": row.packet,
        }
    )


__all__ = [
    "approval_from_orm",
    "approval_from_row_dict",
    "approval_to_orm",
    "approval_to_row_dict",
    "event_from_orm",
    "event_from_row_dict",
    "event_to_orm",
    "event_to_row_dict",
    "run_packet_from_orm",
    "run_packet_from_row_dict",
    "run_packet_to_orm",
    "run_packet_to_row_dict",
    "task_from_orm",
    "task_from_row_dict",
    "task_to_orm",
    "task_to_row_dict",
    "tool_call_from_orm",
    "tool_call_from_row_dict",
    "tool_call_to_orm",
    "tool_call_to_row_dict",
]

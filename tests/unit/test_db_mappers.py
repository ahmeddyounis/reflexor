from __future__ import annotations

import uuid

from reflexor.domain.enums import ApprovalStatus, TaskStatus, ToolCallStatus
from reflexor.domain.models import Approval, Task, ToolCall
from reflexor.domain.models_event import Event
from reflexor.domain.models_run_packet import RunPacket
from reflexor.infra.db.mappers import (
    approval_from_row_dict,
    approval_to_row_dict,
    event_from_row_dict,
    event_to_row_dict,
    run_packet_from_row_dict,
    run_packet_to_row_dict,
    task_from_row_dict,
    task_to_row_dict,
    tool_call_from_row_dict,
    tool_call_to_row_dict,
)


def test_event_mapping_round_trip() -> None:
    event = Event(
        event_id=str(uuid.uuid4()),
        type="ticket.created",
        source="tests",
        received_at_ms=0,
        payload={"ticket_id": "T-1"},
        dedupe_key="ticket:T-1",
    )

    row = event_to_row_dict(event)
    restored = event_from_row_dict(row)
    assert restored.model_dump(mode="json") == event.model_dump(mode="json")


def test_tool_call_mapping_round_trip() -> None:
    tool_call = ToolCall(
        tool_call_id=str(uuid.uuid4()),
        tool_name="mock.echo",
        args={"message": "hello"},
        permission_scope="debug.echo",
        idempotency_key="k1",
        status=ToolCallStatus.SUCCEEDED,
        created_at_ms=0,
        started_at_ms=0,
        completed_at_ms=1,
        result_ref="result:1",
    )

    row = tool_call_to_row_dict(tool_call)
    restored = tool_call_from_row_dict(row)
    assert restored.model_dump(mode="json") == tool_call.model_dump(mode="json")


def test_task_mapping_round_trip_with_tool_call() -> None:
    run_id = str(uuid.uuid4())
    tool_call = ToolCall(
        tool_call_id=str(uuid.uuid4()),
        tool_name="mock.echo",
        args={"message": "hello"},
        permission_scope="debug.echo",
        idempotency_key="k2",
        status=ToolCallStatus.PENDING,
        created_at_ms=0,
    )
    task = Task(
        task_id=str(uuid.uuid4()),
        run_id=run_id,
        name="do thing",
        status=TaskStatus.RUNNING,
        tool_call=tool_call,
        attempts=0,
        max_attempts=2,
        timeout_s=30,
        depends_on=["a", "b"],
        created_at_ms=0,
        started_at_ms=0,
        labels=["l1"],
        metadata={"k": "v"},
    )

    task_row = task_to_row_dict(task)
    tool_call_row = tool_call_to_row_dict(tool_call)
    restored = task_from_row_dict(task_row, tool_call_row=tool_call_row)
    assert restored.model_dump(mode="json") == task.model_dump(mode="json")


def test_task_mapping_round_trip_without_tool_call() -> None:
    task = Task(
        task_id=str(uuid.uuid4()),
        run_id=str(uuid.uuid4()),
        name="no tool",
        created_at_ms=0,
        tool_call=None,
    )

    row = task_to_row_dict(task)
    restored = task_from_row_dict(row)
    assert restored.model_dump(mode="json") == task.model_dump(mode="json")


def test_approval_mapping_round_trip() -> None:
    approval = Approval(
        approval_id=str(uuid.uuid4()),
        run_id=str(uuid.uuid4()),
        task_id=str(uuid.uuid4()),
        tool_call_id=str(uuid.uuid4()),
        status=ApprovalStatus.PENDING,
        created_at_ms=0,
        preview="preview",
        payload_hash="hash",
    )

    row = approval_to_row_dict(approval)
    restored = approval_from_row_dict(row)
    assert restored.model_dump(mode="json") == approval.model_dump(mode="json")


def test_run_packet_mapping_round_trip() -> None:
    run_id = str(uuid.uuid4())
    event = Event(
        event_id=str(uuid.uuid4()),
        type="ping",
        source="tests",
        received_at_ms=0,
        payload={"message": "hello"},
    )
    tool_call = ToolCall(
        tool_call_id=str(uuid.uuid4()),
        tool_name="mock.echo",
        args={"message": "hello"},
        permission_scope="debug.echo",
        idempotency_key="k3",
        status=ToolCallStatus.SUCCEEDED,
        created_at_ms=0,
        started_at_ms=0,
        completed_at_ms=1,
    )
    task = Task(
        task_id=str(uuid.uuid4()),
        run_id=run_id,
        name="echo",
        status=TaskStatus.SUCCEEDED,
        tool_call=tool_call,
        created_at_ms=0,
        started_at_ms=0,
        completed_at_ms=1,
    )
    packet = RunPacket(
        run_id=run_id,
        parent_run_id=str(uuid.uuid4()),
        event=event,
        reflex_decision={"action": "fast_tasks"},
        plan={"summary": "n/a"},
        tasks=[task],
        tool_results=[{"ok": True}],
        policy_decisions=[{"type": "ok"}],
        created_at_ms=0,
        started_at_ms=0,
        completed_at_ms=1,
    )

    row = run_packet_to_row_dict(packet)
    assert row["run_id"] == run_id
    assert row["created_at_ms"] == 0
    restored = run_packet_from_row_dict(row)
    assert restored.model_dump(mode="json") == packet.model_dump(mode="json")

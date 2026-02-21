from __future__ import annotations

import json
import uuid

import pytest

from reflexor.domain.models import Task, ToolCall
from reflexor.domain.models_event import Event
from reflexor.domain.models_run_packet import (
    DEFAULT_MAX_POLICY_DECISION_BYTES,
    DEFAULT_MAX_TOOL_RESULT_BYTES,
    RunPacket,
)


def test_run_packet_build_add_round_trip() -> None:
    run_id = str(uuid.uuid4())
    event = Event(type="example.event", source="tests", received_at_ms=1, payload={"ok": True})
    packet = RunPacket(run_id=run_id, event=event, created_at_ms=0)

    task = Task(run_id=run_id, name="step", created_at_ms=0)
    packet2 = packet.with_task_added(task)
    assert packet.tasks == []
    assert [t.task_id for t in packet2.tasks] == [task.task_id]

    tool_call = ToolCall(
        tool_name="example",
        permission_scope="tests",
        idempotency_key="k1",
        created_at_ms=0,
    )
    tool_result = {"tool_call_id": tool_call.tool_call_id, "ok": True}
    packet3 = packet2.with_tool_result_added(tool_result)
    assert packet3.tool_results == [tool_result]

    policy_decision = {"approval": "approved", "reason": "ok"}
    packet4 = packet3.with_policy_decision_added(policy_decision)
    assert packet4.policy_decisions == [policy_decision]

    dumped = packet4.model_dump()
    restored = RunPacket.model_validate(dumped)
    assert restored.model_dump() == dumped

    as_json = json.loads(packet4.model_dump_json())
    assert as_json["run_id"] == run_id


def test_run_packet_rejects_non_uuid4_run_id() -> None:
    with pytest.raises(ValueError, match="run_id must be a UUID4"):
        RunPacket(
            run_id=str(uuid.uuid1()),
            event=Event(type="t", source="s", received_at_ms=1, payload={}),
        )


def test_run_packet_rejects_large_tool_result_entry() -> None:
    packet = RunPacket(
        run_id=str(uuid.uuid4()),
        event=Event(type="t", source="s", received_at_ms=1, payload={}),
        created_at_ms=0,
    )
    too_large = {"data": "x" * (DEFAULT_MAX_TOOL_RESULT_BYTES + 1)}
    with pytest.raises(ValueError, match="tool_results entry is too large"):
        packet.with_tool_result_added(too_large)


def test_run_packet_rejects_large_policy_decision_entry() -> None:
    packet = RunPacket(
        run_id=str(uuid.uuid4()),
        event=Event(type="t", source="s", received_at_ms=1, payload={}),
        created_at_ms=0,
    )
    too_large = {"data": "x" * (DEFAULT_MAX_POLICY_DECISION_BYTES + 1)}
    with pytest.raises(ValueError, match="policy_decisions entry is too large"):
        packet.with_policy_decision_added(too_large)

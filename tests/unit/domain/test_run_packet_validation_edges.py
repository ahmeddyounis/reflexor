from __future__ import annotations

import math
import uuid

import pytest

from reflexor.domain.models import Task
from reflexor.domain.models_event import Event
from reflexor.domain.models_run_packet import (
    DEFAULT_MAX_PLAN_BYTES,
    DEFAULT_MAX_REFLEX_DECISION_BYTES,
    DEFAULT_MAX_TASKS,
    DEFAULT_MAX_TOOL_RESULT_BYTES,
    RunPacket,
)

RUN_ID = "00000000-0000-4000-8000-000000000000"
OTHER_RUN_ID = "00000000-0000-4000-8000-000000000001"


def _event() -> Event:
    return Event(type="t", source="s", received_at_ms=1, payload={})


def test_run_packet_run_id_and_parent_run_id_validation() -> None:
    with pytest.raises(ValueError, match="run_id is required"):
        RunPacket(run_id=None, event=_event())  # type: ignore[arg-type]

    parent = uuid.UUID("11111111-1111-4111-8111-111111111111")
    packet = RunPacket(run_id=RUN_ID, parent_run_id=parent, event=_event(), created_at_ms=0)
    assert packet.parent_run_id == str(parent)

    with pytest.raises(TypeError, match="parent_run_id must be a UUID or UUID string"):
        RunPacket(run_id=RUN_ID, parent_run_id=123, event=_event())  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="parent_run_id must be a valid UUID"):
        RunPacket(run_id=RUN_ID, parent_run_id="nope", event=_event())

    with pytest.raises(ValueError, match="parent_run_id must be a UUID4"):
        RunPacket(run_id=RUN_ID, parent_run_id=str(uuid.uuid1()), event=_event())

    with pytest.raises(ValueError, match="parent_run_id must differ from run_id"):
        RunPacket(run_id=RUN_ID, parent_run_id=RUN_ID, event=_event())


def test_run_packet_rejects_non_json_and_oversized_reflex_decision() -> None:
    with pytest.raises(ValueError, match="reflex_decision must be valid JSON"):
        RunPacket(run_id=RUN_ID, event=_event(), reflex_decision={"bad": object()})

    with pytest.raises(ValueError, match="reflex_decision must be valid JSON"):
        RunPacket(run_id=RUN_ID, event=_event(), reflex_decision={"bad": math.nan})

    too_large = {"data": "x" * (DEFAULT_MAX_REFLEX_DECISION_BYTES + 10)}
    with pytest.raises(ValueError, match="reflex_decision is too large"):
        RunPacket(run_id=RUN_ID, event=_event(), reflex_decision=too_large)


def test_run_packet_rejects_non_json_and_oversized_plan() -> None:
    with pytest.raises(ValueError, match="plan must be valid JSON"):
        RunPacket(run_id=RUN_ID, event=_event(), plan={"bad": object()})

    with pytest.raises(ValueError, match="plan must be valid JSON"):
        RunPacket(run_id=RUN_ID, event=_event(), plan={"bad": math.inf})

    too_large = {"data": "x" * (DEFAULT_MAX_PLAN_BYTES + 10)}
    with pytest.raises(ValueError, match="plan is too large"):
        RunPacket(run_id=RUN_ID, event=_event(), plan=too_large)


def test_run_packet_rejects_non_json_tool_results_and_policy_decisions() -> None:
    with pytest.raises(ValueError, match="tool_results must be valid JSON"):
        RunPacket(
            run_id=RUN_ID,
            event=_event(),
            created_at_ms=0,
            tool_results=[{"ok": True}, {"bad": object()}],
        )

    with pytest.raises(ValueError, match="tool_results must be valid JSON"):
        RunPacket(
            run_id=RUN_ID,
            event=_event(),
            created_at_ms=0,
            tool_results=[{"bad": math.nan}],
        )

    with pytest.raises(ValueError, match="policy_decisions must be valid JSON"):
        RunPacket(
            run_id=RUN_ID,
            event=_event(),
            created_at_ms=0,
            policy_decisions=[{"bad": object()}],
        )

    with pytest.raises(ValueError, match="policy_decisions must be valid JSON"):
        RunPacket(
            run_id=RUN_ID,
            event=_event(),
            created_at_ms=0,
            policy_decisions=[{"bad": math.inf}],
        )


def test_run_packet_rejects_too_many_tasks_and_mismatched_run_ids() -> None:
    task = Task(run_id=RUN_ID, name="t", created_at_ms=0)
    too_many = [task] * (DEFAULT_MAX_TASKS + 1)
    with pytest.raises(ValueError, match="too many tasks"):
        RunPacket(run_id=RUN_ID, event=_event(), created_at_ms=0, tasks=too_many)

    mismatched_task = Task(run_id=OTHER_RUN_ID, name="t", created_at_ms=0)
    with pytest.raises(ValueError, match="tasks must all share run_id"):
        RunPacket(run_id=RUN_ID, event=_event(), created_at_ms=0, tasks=[mismatched_task])


def test_run_packet_rejects_invalid_timestamp_ordering() -> None:
    with pytest.raises(ValueError, match="started_at_ms must be >= created_at_ms"):
        RunPacket(run_id=RUN_ID, event=_event(), created_at_ms=10, started_at_ms=9)

    with pytest.raises(ValueError, match="completed_at_ms must be >= started_at_ms"):
        RunPacket(
            run_id=RUN_ID, event=_event(), created_at_ms=0, started_at_ms=10, completed_at_ms=9
        )

    with pytest.raises(ValueError, match="completed_at_ms must be >= created_at_ms"):
        RunPacket(run_id=RUN_ID, event=_event(), created_at_ms=10, completed_at_ms=9)


def test_run_packet_rejects_total_size_exceeding_packet_cap() -> None:
    # Keep each entry safely below the per-entry cap, but exceed the packet cap via repetition.
    per_entry_len = DEFAULT_MAX_TOOL_RESULT_BYTES - 1_000
    tool_results = [{"data": "x" * per_entry_len} for _ in range(9)]

    with pytest.raises(ValueError, match="run packet is too large"):
        RunPacket(run_id=RUN_ID, event=_event(), created_at_ms=0, tool_results=tool_results)

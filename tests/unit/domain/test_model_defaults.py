from __future__ import annotations

import uuid

import pytest

from reflexor.domain.models import Approval, Task, ToolCall
from reflexor.domain.models_event import Event
from reflexor.domain.models_run_packet import RunPacket

RUN_ID = "00000000-0000-4000-8000-000000000000"


@pytest.fixture()
def fixed_uuid4() -> uuid.UUID:
    return uuid.UUID("11111111-1111-4111-8111-111111111111")


def test_models_generate_default_ids_and_timestamps(
    monkeypatch: pytest.MonkeyPatch, fixed_uuid4: uuid.UUID
) -> None:
    import reflexor.domain.models as models

    monkeypatch.setattr(models, "uuid4", lambda: fixed_uuid4)
    monkeypatch.setattr(models.time, "time", lambda: 1.234)

    tool_call = ToolCall(tool_name="x", permission_scope="p", idempotency_key="k")
    assert tool_call.tool_call_id == str(fixed_uuid4)
    assert tool_call.created_at_ms == 1234

    task = Task(run_id=RUN_ID, name="t")
    assert task.task_id == str(fixed_uuid4)
    assert task.created_at_ms == 1234

    approval = Approval(run_id=RUN_ID, task_id=RUN_ID, tool_call_id=RUN_ID)
    assert approval.approval_id == str(fixed_uuid4)
    assert approval.created_at_ms == 1234


def test_event_generates_default_event_id(
    monkeypatch: pytest.MonkeyPatch, fixed_uuid4: uuid.UUID
) -> None:
    import reflexor.domain.models_event as models_event

    monkeypatch.setattr(models_event, "uuid4", lambda: fixed_uuid4)
    event = Event(type="t", source="s", received_at_ms=1, payload={})
    assert event.event_id == str(fixed_uuid4)


def test_run_packet_generates_default_created_timestamp(monkeypatch: pytest.MonkeyPatch) -> None:
    import reflexor.domain.models_run_packet as models_run_packet

    monkeypatch.setattr(models_run_packet.time, "time", lambda: 1.234)
    packet = RunPacket(
        run_id=RUN_ID, event=Event(type="t", source="s", received_at_ms=1, payload={})
    )
    assert packet.created_at_ms == 1234

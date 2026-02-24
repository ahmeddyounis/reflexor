from __future__ import annotations

from reflexor.config import ReflexorSettings
from reflexor.domain.models import Task, ToolCall
from reflexor.domain.models_event import Event
from reflexor.domain.models_run_packet import RunPacket
from reflexor.observability.truncation import TRUNCATION_MARKER
from reflexor.orchestrator.sinks import InMemoryRunPacketSink


def _make_packet(*, run_id: str, event_id: str, task_id: str, tool_call_id: str) -> RunPacket:
    event = Event(
        event_id=event_id,
        type="webhook",
        source="tests",
        received_at_ms=1,
        payload={
            "authorization": "Bearer " + ("x" * 50),
            "notes": "n" * 500,
        },
    )

    tool_call = ToolCall(
        tool_call_id=tool_call_id,
        tool_name="net.http",
        args={
            "api_key": "sk-" + ("b" * 30),
            "body": "y" * 2_000,
        },
        permission_scope="net.http",
        idempotency_key="k",
    )

    task = Task(
        task_id=task_id,
        run_id=run_id,
        name="do-thing",
        tool_call=tool_call,
    )

    return RunPacket(
        run_id=run_id,
        event=event,
        reflex_decision={"token": "ghp_" + ("a" * 30), "notes": "r" * 1_000},
        plan={"summary": "plan", "authorization": "Bearer " + ("z" * 40), "blob": "p" * 3_000},
        tasks=[task],
        tool_results=[
            {
                "tool_call_id": tool_call_id,
                "output": {
                    "cookie": "sessionid=abc",
                    "body": "w" * 2_000,
                },
            }
        ],
        policy_decisions=[{"type": "debug", "message": "sk-" + ("q" * 25)}],
    )


async def test_inmemory_run_packet_sink_sanitizes_and_preserves_ids() -> None:
    settings = ReflexorSettings(
        max_event_payload_bytes=80,
        max_tool_output_bytes=80,
        max_run_packet_bytes=10_000,
    )
    sink = InMemoryRunPacketSink(settings=settings)

    run_id = "33333333-3333-4333-8333-333333333333"
    event_id = "44444444-4444-4444-8444-444444444444"
    task_id = "55555555-5555-4555-8555-555555555555"
    tool_call_id = "66666666-6666-4666-8666-666666666666"

    packet = _make_packet(
        run_id=run_id, event_id=event_id, task_id=task_id, tool_call_id=tool_call_id
    )
    await sink.emit(packet)

    stored = await sink.get(run_id)
    assert stored is not None

    assert stored["run_id"] == run_id
    assert stored["event"]["event_id"] == event_id
    assert stored["tasks"][0]["task_id"] == task_id
    assert stored["tasks"][0]["tool_call"]["tool_call_id"] == tool_call_id
    assert stored["tool_results"][0]["tool_call_id"] == tool_call_id

    dump = str(stored)
    assert "sk-" not in dump
    assert "ghp_" not in dump
    assert "Bearer " not in dump
    assert "sessionid=abc" not in dump
    assert "<redacted>" in dump
    assert TRUNCATION_MARKER in dump


async def test_inmemory_run_packet_sink_lists_recent_runs() -> None:
    settings = ReflexorSettings(max_run_packet_bytes=10_000)
    sink = InMemoryRunPacketSink(settings=settings)

    packet1 = _make_packet(
        run_id="77777777-7777-4777-8777-777777777777",
        event_id="88888888-8888-4888-8888-888888888888",
        task_id="99999999-9999-4999-8999-999999999999",
        tool_call_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    )
    packet2 = _make_packet(
        run_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        event_id="cccccccc-cccc-4ccc-8ccc-cccccccccccc",
        task_id="dddddddd-dddd-4ddd-8ddd-dddddddddddd",
        tool_call_id="eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
    )

    await sink.emit(packet1)
    await sink.emit(packet2)

    recent_one = await sink.list_recent(limit=1)
    assert [item["run_id"] for item in recent_one] == ["bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"]

    recent_two = await sink.list_recent(limit=2)
    assert [item["run_id"] for item in recent_two] == [
        "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        "77777777-7777-4777-8777-777777777777",
    ]

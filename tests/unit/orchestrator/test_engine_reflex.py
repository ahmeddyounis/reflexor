from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from prometheus_client import generate_latest
from pydantic import BaseModel

import reflexor.orchestrator.engine.reflex as reflex_module
from reflexor.config import ReflexorSettings
from reflexor.domain.models_event import Event
from reflexor.domain.models_run_packet import RunPacket
from reflexor.domain.serialization import canonical_json, stable_sha256
from reflexor.infra.queue.in_memory_queue import InMemoryQueue
from reflexor.observability.metrics import ReflexorMetrics
from reflexor.orchestrator.budgets import BudgetLimits
from reflexor.orchestrator.clock import Clock
from reflexor.orchestrator.engine import OrchestratorEngine
from reflexor.orchestrator.interfaces import NoOpPlanner
from reflexor.orchestrator.persistence import PersistEventAndRunResult
from reflexor.orchestrator.plans import PlanningInput, ProposedTask, ReflexDecision
from reflexor.orchestrator.reflex_rules import RuleBasedReflexRouter
from reflexor.orchestrator.sinks import InMemoryRunPacketSink
from reflexor.storage.ports import RunRecord
from reflexor.tools.registry import ToolRegistry
from reflexor.tools.sdk import ToolContext, ToolManifest, ToolResult


@dataclass(slots=True)
class _FixedClock(Clock):
    now: int = 123
    monotonic: int = 0

    def now_ms(self) -> int:
        return self.now

    def monotonic_ms(self) -> int:
        return self.monotonic

    async def sleep(self, seconds: float) -> None:
        _ = seconds
        raise AssertionError("sleep should not be called in this test")


class _MockArgs(BaseModel):
    url: str
    event_id: str
    event_type: str
    count: int


class _MockTool:
    manifest = ToolManifest(
        name="tests.mock",
        version="0.1.0",
        description="Mock tool for orchestrator reflex tests.",
        permission_scope="net.http",
        idempotent=True,
        max_output_bytes=10_000,
    )
    ArgsModel = _MockArgs

    async def run(self, args: _MockArgs, ctx: ToolContext) -> ToolResult:  # pragma: no cover
        _ = (args, ctx)
        return ToolResult(ok=True, data={"ok": True})


def _event(tmp_path: Path) -> Event:
    _ = tmp_path
    return Event(
        event_id="11111111-1111-4111-8111-111111111111",
        type="webhook",
        source="tests",
        received_at_ms=0,
        payload={"url": "https://example.com/path"},
    )


class _RecordingPersistence:
    def __init__(self) -> None:
        self.event_run_ids: list[str] = []
        self.task_ids: list[str] = []
        self.finalize_calls: list[tuple[str, list[str]]] = []

    async def persist_event_and_run(
        self, *, event: Event, run_record: RunRecord
    ) -> PersistEventAndRunResult:
        self.event_run_ids.append(run_record.run_id)
        return PersistEventAndRunResult(event=event, created=True)

    async def persist_tasks_and_tool_calls(self, tasks: object) -> None:
        self.task_ids.extend(getattr(task, "task_id", "") for task in tasks)

    async def finalize_run(self, packet: RunPacket, *, enqueued_task_ids: object = ()) -> None:
        self.finalize_calls.append((packet.run_id, list(enqueued_task_ids)))

    async def get_run_id_for_event(self, event_id: str) -> str | None:
        _ = event_id
        return None


class _ExplodingRouter:
    async def route(self, event: Event, planning_input: object) -> object:
        _ = (event, planning_input)
        raise RuntimeError("Bearer sk-raw-secret-should-not-leak")


class _PriorityRouter:
    async def route(self, event: Event, planning_input: PlanningInput) -> ReflexDecision:
        _ = planning_input
        return ReflexDecision(
            action="fast_tasks",
            reason="priority_route",
            proposed_tasks=[
                ProposedTask(
                    name="primary",
                    tool_name="tests.mock",
                    args={
                        "url": event.payload["url"],
                        "event_id": event.event_id,
                        "event_type": event.type,
                        "count": 1,
                    },
                    priority=9,
                ),
                ProposedTask(
                    name="secondary",
                    tool_name="tests.mock",
                    args={
                        "url": event.payload["url"],
                        "event_id": event.event_id,
                        "event_type": event.type,
                        "count": 2,
                    },
                    priority=1,
                ),
            ],
        )


class _SecondEnqueueFailsQueue:
    def __init__(self) -> None:
        self.envelopes = []
        self.calls = 0

    async def enqueue(self, envelope) -> None:
        self.calls += 1
        if self.calls == 2:
            raise RuntimeError("queue unavailable")
        self.envelopes.append(envelope)

    async def dequeue(
        self,
        timeout_s: float | None = None,
        *,
        wait_s: float | None = 0.0,
    ):  # pragma: no cover
        _ = (timeout_s, wait_s)
        raise NotImplementedError

    async def ack(self, lease) -> None:  # pragma: no cover
        _ = lease
        raise NotImplementedError

    async def nack(
        self,
        lease,
        delay_s: float | None = None,
        reason: str | None = None,
    ) -> None:  # pragma: no cover
        _ = (lease, delay_s, reason)
        raise NotImplementedError

    async def aclose(self) -> None:  # pragma: no cover
        return


async def test_reflex_rule_validates_and_enqueues_task_envelope(tmp_path: Path) -> None:
    settings = ReflexorSettings(workspace_root=tmp_path, max_run_packet_bytes=10_000)

    registry = ToolRegistry()
    registry.register(_MockTool())

    router = RuleBasedReflexRouter.from_raw_rules(
        [
            {
                "rule_id": "mock_rule",
                "match": {"event_type": "webhook"},
                "action": {
                    "kind": "fast_tool",
                    "tool_name": "tests.mock",
                    "args_template": {
                        "url": "${payload.url}",
                        "event_id": "${event.event_id}",
                        "event_type": "${event.type}",
                        "count": 1,
                    },
                },
            }
        ]
    )

    clock = _FixedClock()
    queue = InMemoryQueue(now_ms=clock.now_ms)
    sink = InMemoryRunPacketSink(settings=settings)
    persistence = _RecordingPersistence()
    metrics = ReflexorMetrics.build()

    engine = OrchestratorEngine(
        reflex_router=router,
        planner=NoOpPlanner(),
        tool_registry=registry,
        queue=queue,
        run_sink=sink,
        persistence=persistence,
        limits=BudgetLimits(max_tasks_per_run=10, max_tool_calls_per_run=10),
        clock=clock,
        metrics=metrics,
        planner_debounce_s=settings.planner_debounce_s,
        planner_interval_s=settings.planner_interval_s,
        enabled_scopes=("net.http",),
    )

    run_id = await engine.handle_event(_event(tmp_path))
    parsed_run_id = UUID(run_id)
    assert parsed_run_id.version == 4

    lease = await queue.dequeue(wait_s=0.0)
    assert lease is not None
    envelope = lease.envelope
    assert envelope.run_id == run_id
    assert envelope.attempt == 0
    assert envelope.created_at_ms == 123
    assert envelope.available_at_ms == 123

    assert envelope.trace is not None
    assert envelope.trace["source"] == "reflex"
    assert envelope.trace["reason"] == "mock_rule"
    assert envelope.trace["trigger"] == "event"

    assert envelope.payload is not None
    assert envelope.payload["tool_name"] == "tests.mock"
    assert envelope.payload["permission_scope"] == "net.http"
    assert "args" not in envelope.payload

    assert envelope.correlation_ids is not None
    assert envelope.correlation_ids["event_id"] == "11111111-1111-4111-8111-111111111111"
    assert envelope.correlation_ids["run_id"] == run_id
    assert envelope.correlation_ids["task_id"] == envelope.task_id
    assert envelope.correlation_ids["tool_call_id"] == envelope.payload["tool_call_id"]

    await queue.ack(lease)
    assert await queue.dequeue(wait_s=0.0) is None
    assert persistence.event_run_ids == [run_id]
    assert persistence.task_ids == [envelope.task_id]
    assert persistence.finalize_calls == [(run_id, [envelope.task_id])]

    metrics_text = generate_latest(metrics.registry).decode()
    assert "events_received_total 1.0" in metrics_text
    assert "event_to_enqueue_seconds_count 1.0" in metrics_text

    stored = await sink.get(run_id)
    assert stored is not None

    assert stored["run_id"] == run_id
    assert stored["reflex_decision"]["action"] == "fast_tasks"
    assert stored["reflex_decision"]["reason"] == "mock_rule"
    assert len(stored["tasks"]) == 1

    task = stored["tasks"][0]
    assert task["task_id"] == envelope.task_id
    assert task["run_id"] == run_id

    tool_call = task["tool_call"]
    assert tool_call["tool_call_id"] == envelope.payload["tool_call_id"]
    assert tool_call["tool_name"] == "tests.mock"
    assert tool_call["permission_scope"] == "net.http"
    assert tool_call["idempotency_key"] == envelope.payload["idempotency_key"]

    expected_args = {
        "count": 1,
        "event_id": "11111111-1111-4111-8111-111111111111",
        "event_type": "webhook",
        "url": "https://example.com/path",
    }
    expected_key = stable_sha256(
        "tests.mock",
        canonical_json(expected_args),
        "11111111-1111-4111-8111-111111111111",
    )
    assert tool_call["idempotency_key"] == expected_key


async def test_unexpected_reflex_errors_do_not_leak_raw_messages(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class _RecordingLogger:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, object]]] = []

        def error(self, message: str, **kwargs: object) -> None:
            self.calls.append((message, dict(kwargs)))

    settings = ReflexorSettings(workspace_root=tmp_path, max_run_packet_bytes=10_000)
    logger = _RecordingLogger()
    monkeypatch.setattr(reflex_module, "logger", logger)

    clock = _FixedClock()
    queue = InMemoryQueue(now_ms=clock.now_ms)
    sink = InMemoryRunPacketSink(settings=settings)

    engine = OrchestratorEngine(
        reflex_router=_ExplodingRouter(),
        planner=NoOpPlanner(),
        tool_registry=ToolRegistry(),
        queue=queue,
        run_sink=sink,
        limits=BudgetLimits(max_tasks_per_run=10, max_tool_calls_per_run=10),
        clock=clock,
        metrics=ReflexorMetrics.build(),
        enabled_scopes=("net.http",),
    )

    run_id = await engine.handle_event(_event(tmp_path))
    stored = await sink.get(run_id)

    assert stored is not None
    assert stored["policy_decisions"][0]["type"] == "reflex_error"
    assert stored["policy_decisions"][0]["message"] == "unexpected reflex error"
    assert "sk-raw-secret-should-not-leak" not in json.dumps(stored)
    assert logger.calls == [
        (
            "unexpected reflex error",
            {
                "extra": {
                    "run_id": run_id,
                    "event_id": stored["event"]["event_id"],
                    "event_type": stored["event"]["type"],
                    "event_source": stored["event"]["source"],
                    "exception_type": "RuntimeError",
                }
            },
        )
    ]


async def test_reflex_partial_enqueue_records_policy_and_preserves_partial_state(
    tmp_path: Path,
) -> None:
    settings = ReflexorSettings(workspace_root=tmp_path, max_run_packet_bytes=10_000)

    registry = ToolRegistry()
    registry.register(_MockTool())

    queue = _SecondEnqueueFailsQueue()
    sink = InMemoryRunPacketSink(settings=settings)
    persistence = _RecordingPersistence()

    engine = OrchestratorEngine(
        reflex_router=_PriorityRouter(),
        planner=NoOpPlanner(),
        tool_registry=registry,
        queue=queue,
        run_sink=sink,
        persistence=persistence,
        limits=BudgetLimits(max_tasks_per_run=10, max_tool_calls_per_run=10),
        clock=_FixedClock(),
        metrics=ReflexorMetrics.build(),
        enabled_scopes=("net.http",),
    )

    run_id = await engine.handle_event(_event(tmp_path))
    assert len(queue.envelopes) == 1
    assert queue.envelopes[0].priority == 9

    stored = await sink.get(run_id)
    assert stored is not None

    by_name = {task["name"]: task for task in stored["tasks"]}
    assert by_name["primary"]["status"] == "queued"
    assert by_name["secondary"]["status"] == "pending"
    assert stored["policy_decisions"] == [
        {
            "type": "queue_enqueue_error",
            "message": "task enqueue failed",
            "failed_task_id": by_name["secondary"]["task_id"],
        }
    ]
    assert set(persistence.task_ids) == {
        by_name["primary"]["task_id"],
        by_name["secondary"]["task_id"],
    }
    assert persistence.finalize_calls == [(run_id, [by_name["primary"]["task_id"]])]

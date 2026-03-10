from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from reflexor.domain.models_event import Event
from reflexor.domain.serialization import canonical_json, stable_sha256
from reflexor.orchestrator.budgets import BudgetLimits
from reflexor.orchestrator.clock import Clock
from reflexor.orchestrator.engine import OrchestratorEngine
from reflexor.orchestrator.engine import queueing as queueing_module
from reflexor.orchestrator.interfaces import NoOpPlanner
from reflexor.orchestrator.plans import PlanningInput, ProposedTask, ReflexDecision
from reflexor.orchestrator.queue import Lease, TaskEnvelope
from reflexor.orchestrator.reflex_rules import RuleBasedReflexRouter
from reflexor.orchestrator.sinks import RunPacketSink
from reflexor.tools.impl.echo import EchoTool
from reflexor.tools.registry import ToolRegistry


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


class _RecordingQueue:
    def __init__(self) -> None:
        self.envelopes: list[TaskEnvelope] = []

    async def enqueue(self, envelope: TaskEnvelope) -> None:
        self.envelopes.append(envelope)

    async def dequeue(
        self,
        timeout_s: float | None = None,
        *,
        wait_s: float | None = 0.0,
    ) -> Lease | None:  # pragma: no cover
        _ = (timeout_s, wait_s)
        raise NotImplementedError

    async def ack(self, lease: Lease) -> None:  # pragma: no cover
        _ = lease
        raise NotImplementedError

    async def nack(
        self, lease: Lease, delay_s: float | None = None, reason: str | None = None
    ) -> None:  # pragma: no cover
        _ = (lease, delay_s, reason)
        raise NotImplementedError

    async def aclose(self) -> None:  # pragma: no cover
        return


class _InMemoryRunSink(RunPacketSink):
    def __init__(self) -> None:
        self.packets = []

    async def emit(self, packet) -> None:  # type: ignore[override]
        self.packets.append(packet)


def _event() -> Event:
    return Event(
        event_id="11111111-1111-4111-8111-111111111111",
        type="webhook",
        source="tests",
        received_at_ms=0,
        payload={"url": "https://example.com/path"},
    )


async def test_handle_event_enqueues_task_envelope_for_reflex_rule() -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())

    router = RuleBasedReflexRouter.from_raw_rules(
        [
            {
                "rule_id": "echo",
                "match": {"event_type": "webhook"},
                "action": {
                    "kind": "fast_tool",
                    "tool_name": "debug.echo",
                    "args_template": {"url": "${payload.url}", "kind": "${event.type}"},
                },
            }
        ]
    )

    queue = _RecordingQueue()
    sink = _InMemoryRunSink()
    clock = _FixedClock()

    engine = OrchestratorEngine(
        reflex_router=router,
        planner=NoOpPlanner(),
        tool_registry=registry,
        queue=queue,
        limits=BudgetLimits(max_tasks_per_run=10, max_tool_calls_per_run=10),
        clock=clock,
        run_sink=sink,
        enabled_scopes=("fs.read",),
    )

    run_id = await engine.handle_event(_event())
    UUID(run_id)  # validates UUID format

    assert len(queue.envelopes) == 1
    envelope = queue.envelopes[0]
    assert envelope.run_id == run_id
    assert envelope.created_at_ms == 123
    assert envelope.available_at_ms == 123
    assert envelope.payload is not None
    assert "args" not in envelope.payload

    assert envelope.correlation_ids is not None
    assert envelope.correlation_ids["event_id"] == "11111111-1111-4111-8111-111111111111"
    assert envelope.correlation_ids["run_id"] == run_id
    assert envelope.correlation_ids["task_id"] == envelope.task_id
    assert envelope.correlation_ids["tool_call_id"] == envelope.payload["tool_call_id"]

    assert len(sink.packets) == 1
    packet = sink.packets[0]
    assert packet.run_id == run_id
    assert packet.reflex_decision["action"] == "fast_tasks"
    assert packet.reflex_decision["reason"] == "echo"
    assert len(packet.tasks) == 1

    task = packet.tasks[0]
    assert task.task_id == envelope.task_id
    assert task.tool_call is not None
    assert task.tool_call.tool_name == "debug.echo"
    assert task.tool_call.permission_scope == "fs.read"

    expected_key = stable_sha256(
        "debug.echo",
        canonical_json({"kind": "webhook", "url": "https://example.com/path"}),
        "11111111-1111-4111-8111-111111111111",
    )
    assert task.tool_call.idempotency_key == expected_key


async def test_handle_event_enqueues_otel_trace_carrier_when_available(
    monkeypatch,
) -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())

    router = RuleBasedReflexRouter.from_raw_rules(
        [
            {
                "rule_id": "echo",
                "match": {"event_type": "webhook"},
                "action": {
                    "kind": "fast_tool",
                    "tool_name": "debug.echo",
                    "args_template": {"url": "${payload.url}"},
                },
            }
        ]
    )

    monkeypatch.setattr(
        queueing_module,
        "inject_trace_carrier",
        lambda: {"traceparent": "00-abc-123-01"},
    )

    queue = _RecordingQueue()
    engine = OrchestratorEngine(
        reflex_router=router,
        planner=NoOpPlanner(),
        tool_registry=registry,
        queue=queue,
        limits=BudgetLimits(max_tasks_per_run=10, max_tool_calls_per_run=10),
        clock=_FixedClock(),
        run_sink=_InMemoryRunSink(),
        enabled_scopes=("fs.read",),
    )

    await engine.handle_event(_event())

    assert len(queue.envelopes) == 1
    envelope = queue.envelopes[0]
    assert envelope.trace is not None
    assert envelope.trace["otel"] == {"traceparent": "00-abc-123-01"}


class _TwoTaskRouter:
    async def route(self, event: Event, ctx: PlanningInput) -> ReflexDecision:
        _ = (event, ctx)
        return ReflexDecision(
            action="fast_tasks",
            reason="two_tasks",
            proposed_tasks=[
                ProposedTask(name="t1", tool_name="debug.echo", args={"a": 1}),
                ProposedTask(name="t2", tool_name="debug.echo", args={"b": 2}),
            ],
        )


class _DependentTaskRouter:
    async def route(self, event: Event, ctx: PlanningInput) -> ReflexDecision:
        _ = (event, ctx)
        return ReflexDecision(
            action="fast_tasks",
            reason="dependency_chain",
            proposed_tasks=[
                ProposedTask(name="root", tool_name="debug.echo", args={"a": 1}),
                ProposedTask(
                    name="child",
                    tool_name="debug.echo",
                    args={"b": 2},
                    depends_on=["root"],
                ),
            ],
        )


async def test_budget_exceeded_prevents_enqueue_and_is_recorded() -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())

    queue = _RecordingQueue()
    sink = _InMemoryRunSink()
    clock = _FixedClock()

    engine = OrchestratorEngine(
        reflex_router=_TwoTaskRouter(),
        planner=NoOpPlanner(),
        tool_registry=registry,
        queue=queue,
        limits=BudgetLimits(max_tasks_per_run=1, max_tool_calls_per_run=10),
        clock=clock,
        run_sink=sink,
        enabled_scopes=("fs.read",),
    )

    run_id = await engine.handle_event(_event())
    UUID(run_id)

    assert queue.envelopes == []
    assert len(sink.packets) == 1
    packet = sink.packets[0]
    assert packet.run_id == run_id
    assert packet.tasks == []
    assert packet.policy_decisions
    assert packet.policy_decisions[0]["type"] == "budget_exceeded"
    assert packet.policy_decisions[0]["context"]["budget"] == "max_tasks_per_run"


async def test_handle_event_only_enqueues_root_tasks_for_dependency_graph() -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())

    queue = _RecordingQueue()
    sink = _InMemoryRunSink()
    clock = _FixedClock()

    engine = OrchestratorEngine(
        reflex_router=_DependentTaskRouter(),
        planner=NoOpPlanner(),
        tool_registry=registry,
        queue=queue,
        limits=BudgetLimits(max_tasks_per_run=10, max_tool_calls_per_run=10),
        clock=clock,
        run_sink=sink,
        enabled_scopes=("fs.read",),
    )

    run_id = await engine.handle_event(_event())
    UUID(run_id)

    assert len(queue.envelopes) == 1
    packet = sink.packets[0]
    assert len(packet.tasks) == 2

    by_name = {task.name: task for task in packet.tasks}
    assert by_name["root"].status.value == "queued"
    assert by_name["child"].status.value == "pending"
    assert by_name["child"].depends_on == [by_name["root"].task_id]

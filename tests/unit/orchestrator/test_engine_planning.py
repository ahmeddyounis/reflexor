from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from prometheus_client import generate_latest
from pydantic import BaseModel

from reflexor.domain.models_event import Event
from reflexor.domain.models_run_packet import RunPacket
from reflexor.observability.metrics import ReflexorMetrics
from reflexor.orchestrator.budgets import BudgetLimits
from reflexor.orchestrator.clock import Clock
from reflexor.orchestrator.engine import OrchestratorEngine
from reflexor.orchestrator.interfaces import NeedsPlanningRouter
from reflexor.orchestrator.plans import BudgetAssertions, Plan, PlanningInput, ProposedTask
from reflexor.orchestrator.queue import Lease, TaskEnvelope
from reflexor.orchestrator.sinks import RunPacketSink
from reflexor.planning import PlannerMemoryLoadError, PlannerRequestError
from reflexor.tools.registry import ToolRegistry
from reflexor.tools.sdk import ToolContext, ToolManifest, ToolResult


@dataclass(slots=True)
class _ManualClock(Clock):
    now_ms_value: int = 0
    monotonic_ms_value: int = 0
    _condition: asyncio.Condition = field(default_factory=asyncio.Condition)

    def now_ms(self) -> int:
        return self.now_ms_value

    def monotonic_ms(self) -> int:
        return self.monotonic_ms_value

    async def sleep(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("sleep seconds must be >= 0")
        delta_ms = int(float(seconds) * 1000)
        async with self._condition:
            target = self.monotonic_ms_value + delta_ms
            await self._condition.wait_for(lambda: self.monotonic_ms_value >= target)

    async def advance(self, *, seconds: float) -> None:
        delta_ms = int(float(seconds) * 1000)
        async with self._condition:
            self.now_ms_value += delta_ms
            self.monotonic_ms_value += delta_ms
            self._condition.notify_all()
        await asyncio.sleep(0)
        await asyncio.sleep(0)


class _RecordingQueue:
    def __init__(self) -> None:
        self.envelopes: list[TaskEnvelope] = []
        self.enqueued = asyncio.Event()

    async def enqueue(self, envelope: TaskEnvelope) -> None:
        self.envelopes.append(envelope)
        self.enqueued.set()

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
        self.packets: list[RunPacket] = []
        self._condition = asyncio.Condition()

    async def emit(self, packet: RunPacket) -> None:
        async with self._condition:
            self.packets.append(packet)
            self._condition.notify_all()

    async def wait_for_count(self, *, count: int, timeout_s: float = 1.0) -> None:
        async with self._condition:
            await asyncio.wait_for(
                self._condition.wait_for(lambda: len(self.packets) >= count),
                timeout=timeout_s,
            )


def _event(event_id: str) -> Event:
    return Event(
        event_id=event_id,
        type="webhook",
        source="tests",
        received_at_ms=0,
        payload={"url": "https://example.com/path"},
    )


class _MockArgs(BaseModel):
    msg: str
    kind: str


class _MockTool:
    manifest = ToolManifest(
        name="tests.mock",
        version="0.1.0",
        description="Mock tool for orchestrator planning tests.",
        permission_scope="fs.read",
        idempotent=True,
        max_output_bytes=10_000,
    )
    ArgsModel = _MockArgs

    async def run(self, args: _MockArgs, ctx: ToolContext) -> ToolResult:  # pragma: no cover
        _ = (args, ctx)
        return ToolResult(ok=True, data={"ok": True})


class _SingleTaskPlanner:
    def __init__(self) -> None:
        self.calls: list[PlanningInput] = []

    async def plan(self, input: PlanningInput) -> Plan:
        self.calls.append(input)
        return Plan(
            summary="planned",
            tasks=[
                ProposedTask(
                    name="mock",
                    tool_name="tests.mock",
                    args={"msg": "hi", "kind": input.trigger},
                )
            ],
            budget_assertions=BudgetAssertions(
                max_tasks=1,
                max_tool_calls=int(input.limits.max_tool_calls or 1),
                max_runtime_s=float(input.limits.max_runtime_s or 30.0),
                max_tokens=int(input.limits.max_tokens or 128),
            ),
            metadata={},
        )


class _InvalidToolPlanner:
    async def plan(self, input: PlanningInput) -> Plan:
        _ = input
        return Plan(
            summary="invalid",
            tasks=[ProposedTask(name="bad", tool_name="missing.tool", args={})],
            budget_assertions=BudgetAssertions(
                max_tasks=1,
                max_tool_calls=1,
                max_runtime_s=30.0,
                max_tokens=128,
            ),
            metadata={},
        )


class _TickPlanner:
    async def plan(self, input: PlanningInput) -> Plan:
        if input.trigger != "tick":
            raise AssertionError(f"expected trigger=tick, got {input.trigger!r}")
        return Plan(
            summary="tick_plan",
            tasks=[
                ProposedTask(
                    name="mock",
                    tool_name="tests.mock",
                    args={"msg": "hi", "kind": "tick"},
                )
            ],
            budget_assertions=BudgetAssertions(
                max_tasks=1,
                max_tool_calls=int(input.limits.max_tool_calls or 1),
                max_runtime_s=float(input.limits.max_runtime_s or 30.0),
                max_tokens=int(input.limits.max_tokens or 128),
            ),
            metadata={},
        )


class _PlannerBackendFailure:
    async def plan(self, input: PlanningInput) -> Plan:
        _ = input
        raise PlannerRequestError("planner backend request failed")


class _PlannerMemoryFailure:
    async def plan(self, input: PlanningInput) -> Plan:
        _ = input
        raise PlannerMemoryLoadError("planner memory loading failed")


async def test_event_driven_planning_enqueues_tasks_and_clears_backlog() -> None:
    registry = ToolRegistry()
    registry.register(_MockTool())

    queue = _RecordingQueue()
    sink = _InMemoryRunSink()
    clock = _ManualClock()
    planner = _SingleTaskPlanner()
    metrics = ReflexorMetrics.build()

    engine = OrchestratorEngine(
        reflex_router=NeedsPlanningRouter(),
        planner=planner,
        tool_registry=registry,
        queue=queue,
        limits=BudgetLimits(max_events_per_planning_cycle=10),
        clock=clock,
        run_sink=sink,
        metrics=metrics,
        planner_debounce_s=1.0,
        planner_interval_s=10_000.0,
        enabled_scopes=("fs.read",),
    )
    engine.start()

    try:
        await engine.handle_event(_event("11111111-1111-4111-8111-111111111111"))
        async with engine._backlog_lock:
            assert len(engine._backlog) == 1
        assert planner.calls == []
        assert queue.envelopes == []

        await clock.advance(seconds=0.9)
        assert planner.calls == []
        assert queue.envelopes == []

        await clock.advance(seconds=0.1)
        await asyncio.wait_for(queue.enqueued.wait(), timeout=1.0)
        await sink.wait_for_count(count=2)

        metrics_text = generate_latest(metrics.registry).decode()
        assert "planner_latency_seconds_count 1.0" in metrics_text

        assert len(queue.envelopes) == 1
        envelope = queue.envelopes[0]
        assert envelope.trace is not None
        assert envelope.trace["source"] == "planner"
        assert envelope.trace["trigger"] == "event"
        assert envelope.payload is not None
        assert "args" not in envelope.payload

        drained = await engine.drain_backlog(max_items=10)
        assert drained == []

        planning_packet = sink.packets[1]
        assert planning_packet.event.type == "planning_cycle"
        assert planning_packet.plan["summary"] == "planned"
        assert len(planning_packet.tasks) == 1

        assert planner.calls
        assert planner.calls[0].trigger == "event"
        assert len(planner.calls[0].events) == 1
    finally:
        await engine.aclose()


async def test_debounce_coalesces_many_event_triggers_into_one_planning_call() -> None:
    registry = ToolRegistry()
    registry.register(_MockTool())

    queue = _RecordingQueue()
    sink = _InMemoryRunSink()
    clock = _ManualClock()
    planner = _SingleTaskPlanner()

    engine = OrchestratorEngine(
        reflex_router=NeedsPlanningRouter(),
        planner=planner,
        tool_registry=registry,
        queue=queue,
        limits=BudgetLimits(max_events_per_planning_cycle=10),
        clock=clock,
        run_sink=sink,
        planner_debounce_s=2.0,
        planner_interval_s=10_000.0,
        enabled_scopes=("fs.read",),
    )
    engine.start()

    try:
        await engine.handle_event(_event("11111111-1111-4111-8111-111111111111"))
        await clock.advance(seconds=0.5)
        await engine.handle_event(_event("22222222-2222-4222-8222-222222222222"))
        await clock.advance(seconds=0.5)
        await engine.handle_event(_event("33333333-3333-4333-8333-333333333333"))

        async with engine._backlog_lock:
            assert len(engine._backlog) == 3

        await clock.advance(seconds=1.9)
        assert planner.calls == []
        assert queue.envelopes == []

        await clock.advance(seconds=0.1)
        await asyncio.wait_for(queue.enqueued.wait(), timeout=1.0)
        await sink.wait_for_count(count=4)

        assert len(planner.calls) == 1
        assert planner.calls[0].trigger == "event"
        assert [event.event_id for event in planner.calls[0].events] == [
            "11111111-1111-4111-8111-111111111111",
            "22222222-2222-4222-8222-222222222222",
            "33333333-3333-4333-8333-333333333333",
        ]

        drained = await engine.drain_backlog(max_items=10)
        assert drained == []

        assert len(queue.envelopes) == 1
    finally:
        await engine.aclose()


async def test_invalid_plan_does_not_clear_backlog() -> None:
    registry = ToolRegistry()
    registry.register(_MockTool())

    queue = _RecordingQueue()
    sink = _InMemoryRunSink()
    clock = _ManualClock()

    engine = OrchestratorEngine(
        reflex_router=NeedsPlanningRouter(),
        planner=_InvalidToolPlanner(),
        tool_registry=registry,
        queue=queue,
        limits=BudgetLimits(max_events_per_planning_cycle=10),
        clock=clock,
        run_sink=sink,
        planner_debounce_s=1.0,
        planner_interval_s=10_000.0,
        enabled_scopes=("fs.read",),
    )
    engine.start()

    try:
        await engine.handle_event(_event("22222222-2222-4222-8222-222222222222"))
        await clock.advance(seconds=1.0)
        await sink.wait_for_count(count=2)

        assert queue.envelopes == []
        drained = await engine.drain_backlog(max_items=10)
        assert len(drained) == 1
        assert drained[0].event_id == "22222222-2222-4222-8222-222222222222"

        planning_packet = sink.packets[1]
        assert planning_packet.event.type == "planning_cycle"
        assert planning_packet.policy_decisions
        assert planning_packet.policy_decisions[0]["type"] == "plan_validation_error"
    finally:
        await engine.aclose()


async def test_tick_path_runs_even_without_event_triggers() -> None:
    registry = ToolRegistry()
    registry.register(_MockTool())

    queue = _RecordingQueue()
    sink = _InMemoryRunSink()
    clock = _ManualClock()

    engine = OrchestratorEngine(
        reflex_router=NeedsPlanningRouter(),
        planner=_TickPlanner(),
        tool_registry=registry,
        queue=queue,
        limits=BudgetLimits(max_events_per_planning_cycle=10),
        clock=clock,
        run_sink=sink,
        planner_debounce_s=10.0,
        planner_interval_s=5.0,
        enabled_scopes=("fs.read",),
    )
    engine.start()

    try:
        await clock.advance(seconds=5.0)
        await asyncio.wait_for(queue.enqueued.wait(), timeout=1.0)
        await sink.wait_for_count(count=1)

        assert len(queue.envelopes) == 1
        envelope = queue.envelopes[0]
        assert envelope.trace is not None
        assert envelope.trace["source"] == "planner"
        assert envelope.trace["trigger"] == "tick"

        assert len(sink.packets) == 1
        planning_packet = sink.packets[0]
        assert planning_packet.event.type == "planning_cycle"
        assert planning_packet.plan["summary"] == "tick_plan"
    finally:
        await engine.aclose()


async def test_planner_backend_failures_emit_structured_policy_decision() -> None:
    registry = ToolRegistry()
    registry.register(_MockTool())

    queue = _RecordingQueue()
    sink = _InMemoryRunSink()
    clock = _ManualClock()
    metrics = ReflexorMetrics.build()

    engine = OrchestratorEngine(
        reflex_router=NeedsPlanningRouter(),
        planner=_PlannerBackendFailure(),
        tool_registry=registry,
        queue=queue,
        limits=BudgetLimits(max_events_per_planning_cycle=10),
        clock=clock,
        run_sink=sink,
        metrics=metrics,
        planner_debounce_s=1.0,
        planner_interval_s=10_000.0,
        enabled_scopes=("fs.read",),
    )
    engine.start()

    try:
        await engine.handle_event(_event("44444444-4444-4444-8444-444444444444"))
        await clock.advance(seconds=1.0)
        await sink.wait_for_count(count=2)

        assert queue.envelopes == []
        planning_packet = sink.packets[1]
        assert planning_packet.policy_decisions == [
            {
                "type": "planning_backend_error",
                "message": "planner backend request failed",
            }
        ]

        metrics_text = generate_latest(metrics.registry).decode()
        assert 'orchestrator_rejections_total{reason="planner_backend"} 1.0' in metrics_text
    finally:
        await engine.aclose()


async def test_planner_memory_failures_emit_structured_policy_decision() -> None:
    registry = ToolRegistry()
    registry.register(_MockTool())

    queue = _RecordingQueue()
    sink = _InMemoryRunSink()
    clock = _ManualClock()
    metrics = ReflexorMetrics.build()

    engine = OrchestratorEngine(
        reflex_router=NeedsPlanningRouter(),
        planner=_PlannerMemoryFailure(),
        tool_registry=registry,
        queue=queue,
        limits=BudgetLimits(max_events_per_planning_cycle=10),
        clock=clock,
        run_sink=sink,
        metrics=metrics,
        planner_debounce_s=1.0,
        planner_interval_s=10_000.0,
        enabled_scopes=("fs.read",),
    )
    engine.start()

    try:
        await engine.handle_event(_event("55555555-5555-4555-8555-555555555555"))
        await clock.advance(seconds=1.0)
        await sink.wait_for_count(count=2)

        assert queue.envelopes == []
        planning_packet = sink.packets[1]
        assert planning_packet.policy_decisions == [
            {
                "type": "planning_memory_error",
                "message": "planner memory loading failed",
            }
        ]

        metrics_text = generate_latest(metrics.registry).decode()
        assert 'orchestrator_rejections_total{reason="memory"} 1.0' in metrics_text
    finally:
        await engine.aclose()

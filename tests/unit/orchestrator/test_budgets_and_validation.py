from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import pytest
from prometheus_client import generate_latest
from pydantic import BaseModel

from reflexor.config import ReflexorSettings, clear_settings_cache
from reflexor.domain.models_event import Event
from reflexor.infra.queue.in_memory_queue import InMemoryQueue
from reflexor.observability.metrics import ReflexorMetrics
from reflexor.orchestrator.budgets import BudgetLimits
from reflexor.orchestrator.clock import Clock
from reflexor.orchestrator.engine import OrchestratorEngine
from reflexor.orchestrator.interfaces import NeedsPlanningRouter, NoOpPlanner
from reflexor.orchestrator.plans import (
    BudgetAssertions,
    Plan,
    PlanningInput,
    ProposedTask,
    ReflexDecision,
)
from reflexor.orchestrator.reflex_rules import RuleBasedReflexRouter
from reflexor.orchestrator.sinks import InMemoryRunPacketSink
from reflexor.tools.registry import ToolRegistry
from reflexor.tools.sdk import ToolContext, ToolManifest, ToolResult


@pytest.fixture(autouse=True)
def _isolate_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_settings_cache()
    for key in list(os.environ):
        if key.startswith("REFLEXOR_"):
            monkeypatch.delenv(key, raising=False)


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


def _event(event_id: str) -> Event:
    return Event(
        event_id=event_id,
        type="webhook",
        source="tests",
        received_at_ms=0,
        payload={"k": "v"},
    )


class _CountArgs(BaseModel):
    count: int


class _CountTool:
    manifest = ToolManifest(
        name="tests.count",
        version="0.1.0",
        description="Count tool for orchestrator budget/validation tests.",
        permission_scope="fs.read",
        idempotent=True,
        max_output_bytes=10_000,
    )
    ArgsModel = _CountArgs

    async def run(self, args: _CountArgs, ctx: ToolContext) -> ToolResult:  # pragma: no cover
        _ = (args, ctx)
        return ToolResult(ok=True, data={"ok": True})


class _TwoTaskRouter:
    async def route(self, event: Event, ctx: PlanningInput) -> ReflexDecision:
        _ = (event, ctx)
        return ReflexDecision(
            action="fast_tasks",
            reason="too_many_tasks",
            proposed_tasks=[
                ProposedTask(name="t1", tool_name="tests.count", args={"count": 1}),
                ProposedTask(name="t2", tool_name="tests.count", args={"count": 2}),
            ],
        )


class _InvalidArgsRouter:
    async def route(self, event: Event, ctx: PlanningInput) -> ReflexDecision:
        _ = (event, ctx)
        return ReflexDecision(
            action="fast_tasks",
            reason="invalid_args",
            proposed_tasks=[
                ProposedTask(name="t1", tool_name="tests.count", args={"count": "nope"})
            ],
        )


class _TaskListPlanner:
    def __init__(self, tasks: list[ProposedTask]) -> None:
        self.calls: list[PlanningInput] = []
        self._tasks = list(tasks)

    async def plan(self, input: PlanningInput) -> Plan:
        self.calls.append(input)
        return Plan(
            summary="planned",
            tasks=list(self._tasks),
            budget_assertions=BudgetAssertions(
                max_tasks=int(input.limits.max_tasks or max(len(self._tasks), 1)),
                max_tool_calls=int(input.limits.max_tool_calls or max(len(self._tasks), 1)),
                max_runtime_s=float(input.limits.max_runtime_s or 30.0),
                max_tokens=int(input.limits.max_tokens or 128),
            ),
            metadata={},
        )


class _SlowNeedsPlanningRouter:
    def __init__(self, clock: _FixedClock, *, elapsed_ms: int) -> None:
        self._clock = clock
        self._elapsed_ms = elapsed_ms

    async def route(self, event: Event, ctx: PlanningInput) -> ReflexDecision:
        _ = (event, ctx)
        self._clock.monotonic += self._elapsed_ms
        return ReflexDecision(action="needs_planning", reason="slow_route", proposed_tasks=[])


class _SlowEmptyPlanner:
    def __init__(self, clock: _FixedClock, *, elapsed_ms: int) -> None:
        self._clock = clock
        self._elapsed_ms = elapsed_ms
        self.calls: list[PlanningInput] = []

    async def plan(self, input: PlanningInput) -> Plan:
        self.calls.append(input)
        self._clock.monotonic += self._elapsed_ms
        return Plan(
            summary="slow_empty",
            tasks=[],
            budget_assertions=BudgetAssertions(
                max_tasks=int(input.limits.max_tasks or 1),
                max_tool_calls=int(input.limits.max_tool_calls or 1),
                max_runtime_s=float(input.limits.max_runtime_s or 30.0),
                max_tokens=int(input.limits.max_tokens or 128),
            ),
            metadata={},
        )


async def test_planning_budget_exceeded_prevents_enqueue_and_is_recorded(tmp_path: Path) -> None:
    settings = ReflexorSettings(workspace_root=tmp_path, max_run_packet_bytes=50_000)

    registry = ToolRegistry()
    registry.register(_CountTool())

    clock = _FixedClock()
    queue = InMemoryQueue(now_ms=clock.now_ms)
    sink = InMemoryRunPacketSink(settings=settings)
    metrics = ReflexorMetrics.build()

    planner = _TaskListPlanner(
        tasks=[
            ProposedTask(name="t1", tool_name="tests.count", args={"count": 1}),
            ProposedTask(name="t2", tool_name="tests.count", args={"count": 2}),
        ]
    )

    engine = OrchestratorEngine(
        reflex_router=NeedsPlanningRouter(),
        planner=planner,
        tool_registry=registry,
        queue=queue,
        run_sink=sink,
        limits=BudgetLimits(
            max_tasks_per_run=1,
            max_tool_calls_per_run=10,
            max_events_per_planning_cycle=10,
        ),
        clock=clock,
        metrics=metrics,
        enabled_scopes=("fs.read",),
    )

    await engine.handle_event(_event("11111111-1111-4111-8111-111111111111"))
    planning_run_id = await engine.run_planning_once(trigger="event")

    assert await queue.dequeue(wait_s=0.0) is None
    drained = await engine.drain_backlog(max_items=10)
    assert [item.event_id for item in drained] == ["11111111-1111-4111-8111-111111111111"]

    stored = await sink.get(planning_run_id)
    assert stored is not None
    assert stored["run_id"] == planning_run_id
    assert stored["event"]["type"] == "planning_cycle"
    UUID(stored["event"]["event_id"])

    assert stored["tasks"] == []
    assert stored["policy_decisions"][0]["type"] == "budget_exceeded"
    assert stored["policy_decisions"][0]["context"]["budget"] == "max_tasks_per_run"

    metrics_text = generate_latest(metrics.registry).decode()
    assert 'orchestrator_rejections_total{reason="budget"} 1.0' in metrics_text


async def test_reflex_budget_exceeded_prevents_enqueue_and_is_recorded(tmp_path: Path) -> None:
    settings = ReflexorSettings(workspace_root=tmp_path, max_run_packet_bytes=50_000)

    registry = ToolRegistry()
    registry.register(_CountTool())

    clock = _FixedClock()
    queue = InMemoryQueue(now_ms=clock.now_ms)
    sink = InMemoryRunPacketSink(settings=settings)
    metrics = ReflexorMetrics.build()

    engine = OrchestratorEngine(
        reflex_router=_TwoTaskRouter(),
        planner=NoOpPlanner(),
        tool_registry=registry,
        queue=queue,
        run_sink=sink,
        limits=BudgetLimits(
            max_tasks_per_run=1,
            max_tool_calls_per_run=10,
        ),
        clock=clock,
        metrics=metrics,
        enabled_scopes=("fs.read",),
    )

    run_id = await engine.handle_event(_event("22222222-2222-4222-8222-222222222222"))
    UUID(run_id)

    assert await queue.dequeue(wait_s=0.0) is None

    stored = await sink.get(run_id)
    assert stored is not None
    assert stored["run_id"] == run_id
    assert stored["event"]["event_id"] == "22222222-2222-4222-8222-222222222222"

    assert stored["tasks"] == []
    assert stored["reflex_decision"]["action"] == "fast_tasks"
    assert stored["reflex_decision"]["reason"] == "too_many_tasks"
    assert stored["policy_decisions"][0]["type"] == "budget_exceeded"
    assert stored["policy_decisions"][0]["context"]["budget"] == "max_tasks_per_run"

    metrics_text = generate_latest(metrics.registry).decode()
    assert 'orchestrator_rejections_total{reason="budget"} 1.0' in metrics_text


async def test_reflex_invalid_args_records_validation_error_and_does_not_enqueue(
    tmp_path: Path,
) -> None:
    settings = ReflexorSettings(workspace_root=tmp_path, max_run_packet_bytes=50_000)

    registry = ToolRegistry()
    registry.register(_CountTool())

    clock = _FixedClock()
    queue = InMemoryQueue(now_ms=clock.now_ms)
    sink = InMemoryRunPacketSink(settings=settings)
    metrics = ReflexorMetrics.build()

    engine = OrchestratorEngine(
        reflex_router=_InvalidArgsRouter(),
        planner=NoOpPlanner(),
        tool_registry=registry,
        queue=queue,
        run_sink=sink,
        limits=BudgetLimits(max_tasks_per_run=10, max_tool_calls_per_run=10),
        clock=clock,
        metrics=metrics,
        enabled_scopes=("fs.read",),
    )

    run_id = await engine.handle_event(_event("33333333-3333-4333-8333-333333333333"))
    UUID(run_id)

    assert await queue.dequeue(wait_s=0.0) is None

    stored = await sink.get(run_id)
    assert stored is not None
    assert stored["run_id"] == run_id
    assert stored["event"]["event_id"] == "33333333-3333-4333-8333-333333333333"

    assert stored["tasks"] == []
    assert stored["reflex_decision"]["action"] == "fast_tasks"
    assert stored["reflex_decision"]["reason"] == "invalid_args"
    assert stored["policy_decisions"][0]["type"] == "plan_validation_error"
    assert "invalid tool args" in stored["policy_decisions"][0]["message"]

    metrics_text = generate_latest(metrics.registry).decode()
    assert 'orchestrator_rejections_total{reason="validation"} 1.0' in metrics_text


async def test_reflex_wall_time_budget_blocks_backlog_enqueue(tmp_path: Path) -> None:
    settings = ReflexorSettings(workspace_root=tmp_path, max_run_packet_bytes=50_000)

    registry = ToolRegistry()
    registry.register(_CountTool())

    clock = _FixedClock()
    queue = InMemoryQueue(now_ms=clock.now_ms)
    sink = InMemoryRunPacketSink(settings=settings)
    metrics = ReflexorMetrics.build()

    engine = OrchestratorEngine(
        reflex_router=_SlowNeedsPlanningRouter(clock, elapsed_ms=1_500),
        planner=NoOpPlanner(),
        tool_registry=registry,
        queue=queue,
        run_sink=sink,
        limits=BudgetLimits(max_wall_time_s=1.0, max_backlog_events=10),
        clock=clock,
        metrics=metrics,
        enabled_scopes=("fs.read",),
    )

    run_id = await engine.handle_event(_event("66666666-6666-4666-8666-666666666666"))

    stored = await sink.get(run_id)
    assert stored is not None
    assert stored["tasks"] == []
    assert stored["policy_decisions"][0]["type"] == "budget_exceeded"
    assert stored["policy_decisions"][0]["context"]["budget"] == "max_wall_time_s"
    assert await engine.drain_backlog(max_items=10) == []

    metrics_text = generate_latest(metrics.registry).decode()
    assert 'orchestrator_rejections_total{reason="budget"} 1.0' in metrics_text


async def test_reflex_template_error_is_recorded_and_does_not_enqueue(tmp_path: Path) -> None:
    settings = ReflexorSettings(workspace_root=tmp_path, max_run_packet_bytes=50_000)

    registry = ToolRegistry()
    registry.register(_CountTool())

    clock = _FixedClock()
    queue = InMemoryQueue(now_ms=clock.now_ms)
    sink = InMemoryRunPacketSink(settings=settings)
    metrics = ReflexorMetrics.build()

    router = RuleBasedReflexRouter.from_raw_rules(
        [
            {
                "rule_id": "missing_count",
                "match": {"event_type": "webhook"},
                "action": {
                    "kind": "fast_tool",
                    "tool_name": "tests.count",
                    "args_template": {"count": "${payload.missing}"},
                },
            }
        ]
    )

    engine = OrchestratorEngine(
        reflex_router=router,
        planner=NoOpPlanner(),
        tool_registry=registry,
        queue=queue,
        run_sink=sink,
        limits=BudgetLimits(max_tasks_per_run=10, max_tool_calls_per_run=10),
        clock=clock,
        metrics=metrics,
        enabled_scopes=("fs.read",),
    )

    run_id = await engine.handle_event(_event("55555555-5555-4555-8555-555555555555"))
    UUID(run_id)

    assert await queue.dequeue(wait_s=0.0) is None

    stored = await sink.get(run_id)
    assert stored is not None
    assert stored["tasks"] == []
    assert stored["policy_decisions"][0]["type"] == "template_resolution_error"
    assert "rule missing_count" in stored["policy_decisions"][0]["message"]
    assert "payload missing key 'missing'" in stored["policy_decisions"][0]["message"]

    metrics_text = generate_latest(metrics.registry).decode()
    assert 'orchestrator_rejections_total{reason="template"} 1.0' in metrics_text


@pytest.mark.parametrize(
    ("case_id", "tasks", "expected_message_substring"),
    [
        (
            "unknown_tool",
            [ProposedTask(name="t1", tool_name="missing.tool", args={"count": 1})],
            "unknown tool",
        ),
        (
            "invalid_args",
            [ProposedTask(name="t1", tool_name="tests.count", args={"count": "nope"})],
            "invalid tool args",
        ),
    ],
)
async def test_planning_validation_failure_is_recorded_and_backlog_is_retained(
    case_id: str,
    tasks: list[ProposedTask],
    expected_message_substring: str,
    tmp_path: Path,
) -> None:
    _ = case_id
    settings = ReflexorSettings(workspace_root=tmp_path, max_run_packet_bytes=50_000)

    registry = ToolRegistry()
    registry.register(_CountTool())

    clock = _FixedClock()
    queue = InMemoryQueue(now_ms=clock.now_ms)
    sink = InMemoryRunPacketSink(settings=settings)

    planner = _TaskListPlanner(tasks=tasks)
    metrics = ReflexorMetrics.build()

    engine = OrchestratorEngine(
        reflex_router=NeedsPlanningRouter(),
        planner=planner,
        tool_registry=registry,
        queue=queue,
        run_sink=sink,
        limits=BudgetLimits(max_tasks_per_run=10, max_tool_calls_per_run=10),
        clock=clock,
        metrics=metrics,
        enabled_scopes=("fs.read",),
    )

    await engine.handle_event(_event("44444444-4444-4444-8444-444444444444"))
    planning_run_id = await engine.run_planning_once(trigger="event")

    assert len(planner.calls) == 1
    assert planner.calls[0].trigger == "event"
    assert [event.event_id for event in planner.calls[0].events] == [
        "44444444-4444-4444-8444-444444444444"
    ]

    assert await queue.dequeue(wait_s=0.0) is None
    drained = await engine.drain_backlog(max_items=10)
    assert [item.event_id for item in drained] == ["44444444-4444-4444-8444-444444444444"]

    stored = await sink.get(planning_run_id)
    assert stored is not None
    assert stored["run_id"] == planning_run_id
    assert stored["event"]["type"] == "planning_cycle"
    UUID(stored["event"]["event_id"])

    assert stored["tasks"] == []
    assert stored["policy_decisions"][0]["type"] == "plan_validation_error"
    assert expected_message_substring in stored["policy_decisions"][0]["message"]

    metrics_text = generate_latest(metrics.registry).decode()
    assert 'orchestrator_rejections_total{reason="validation"} 1.0' in metrics_text


async def test_planning_wall_time_budget_rejects_empty_plan_and_keeps_backlog(
    tmp_path: Path,
) -> None:
    settings = ReflexorSettings(workspace_root=tmp_path, max_run_packet_bytes=50_000)

    registry = ToolRegistry()
    registry.register(_CountTool())

    clock = _FixedClock()
    queue = InMemoryQueue(now_ms=clock.now_ms)
    sink = InMemoryRunPacketSink(settings=settings)
    metrics = ReflexorMetrics.build()
    planner = _SlowEmptyPlanner(clock, elapsed_ms=1_500)

    engine = OrchestratorEngine(
        reflex_router=NeedsPlanningRouter(),
        planner=planner,
        tool_registry=registry,
        queue=queue,
        run_sink=sink,
        limits=BudgetLimits(
            max_wall_time_s=1.0,
            max_events_per_planning_cycle=10,
            max_backlog_events=10,
        ),
        clock=clock,
        metrics=metrics,
        enabled_scopes=("fs.read",),
    )

    await engine.handle_event(_event("77777777-7777-4777-8777-777777777777"))
    planning_run_id = await engine.run_planning_once(trigger="event")

    stored = await sink.get(planning_run_id)
    assert stored is not None
    assert stored["tasks"] == []
    assert stored["policy_decisions"][0]["type"] == "budget_exceeded"
    assert stored["policy_decisions"][0]["context"]["budget"] == "max_wall_time_s"

    drained = await engine.drain_backlog(max_items=10)
    assert [item.event_id for item in drained] == ["77777777-7777-4777-8777-777777777777"]

    metrics_text = generate_latest(metrics.registry).decode()
    assert 'orchestrator_rejections_total{reason="budget"} 1.0' in metrics_text


async def test_backlog_budget_records_event_context(tmp_path: Path) -> None:
    settings = ReflexorSettings(workspace_root=tmp_path, max_run_packet_bytes=50_000)

    registry = ToolRegistry()
    registry.register(_CountTool())

    clock = _FixedClock()
    queue = InMemoryQueue(now_ms=clock.now_ms)
    sink = InMemoryRunPacketSink(settings=settings)
    metrics = ReflexorMetrics.build()

    engine = OrchestratorEngine(
        reflex_router=NeedsPlanningRouter(),
        planner=NoOpPlanner(),
        tool_registry=registry,
        queue=queue,
        run_sink=sink,
        limits=BudgetLimits(max_backlog_events=1),
        clock=clock,
        metrics=metrics,
        enabled_scopes=("fs.read",),
    )

    await engine.handle_event(_event("88888888-8888-4888-8888-888888888888"))
    run_id = await engine.handle_event(_event("99999999-9999-4999-8999-999999999999"))

    stored = await sink.get(run_id)
    assert stored is not None
    assert stored["tasks"] == []
    assert stored["policy_decisions"][0]["type"] == "budget_exceeded"
    assert stored["policy_decisions"][0]["context"] == {
        "budget": "max_backlog_events",
        "limit": 1,
        "current": 1,
        "would_be": 2,
        "event_id": "99999999-9999-4999-8999-999999999999",
        "event_type": "webhook",
        "event_source": "tests",
    }

    drained = await engine.drain_backlog(max_items=10)
    assert [item.event_id for item in drained] == ["88888888-8888-4888-8888-888888888888"]

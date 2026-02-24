from __future__ import annotations

import pytest

from reflexor.domain.models_event import Event
from reflexor.orchestrator.interfaces import NeedsPlanningRouter, NoOpPlanner
from reflexor.orchestrator.plans import Plan, PlanningInput, ProposedTask, ReflexDecision


def _event() -> Event:
    return Event(type="test", source="tests", received_at_ms=0, payload={"x": 1})


def test_proposed_task_json_round_trip() -> None:
    task = ProposedTask(
        name="task-1",
        tool_name="echo",
        args={"x": 1},
        depends_on=["task-0"],
        timeout_s=10,
        max_attempts=2,
        priority=1,
        idempotency_seed="seed",
    )

    dumped = task.model_dump(mode="json")
    assert ProposedTask.model_validate(dumped) == task


def test_proposed_task_rejects_blank_name() -> None:
    with pytest.raises(ValueError, match="name must be non-empty"):
        ProposedTask(name=" ", tool_name="echo")


def test_proposed_task_rejects_non_json_args() -> None:
    with pytest.raises(ValueError, match="args must be JSON-serializable"):
        ProposedTask(name="task-1", tool_name="echo", args={"x": object()})


def test_reflex_decision_json_round_trip() -> None:
    decision = ReflexDecision(action="needs_planning", reason="tests", proposed_tasks=[])
    dumped = decision.model_dump(mode="json")
    assert ReflexDecision.model_validate(dumped) == decision


def test_plan_json_round_trip() -> None:
    plan = Plan(summary="tests", tasks=[ProposedTask(name="task-1", tool_name="echo")])
    dumped = plan.model_dump(mode="json")
    assert Plan.model_validate(dumped) == plan


def test_planning_input_requires_events_for_event_trigger() -> None:
    with pytest.raises(ValueError, match="events must be non-empty"):
        PlanningInput(trigger="event", events=[], now_ms=0)

    ok = PlanningInput(trigger="tick", events=[], now_ms=0)
    assert ok.trigger == "tick"


async def test_stub_planner_and_router_conformance() -> None:
    event = _event()
    input_ = PlanningInput(trigger="event", events=[event], now_ms=0)

    planner = NoOpPlanner()
    plan = await planner.plan(input_)
    assert plan.summary == "noop"
    assert plan.tasks == []

    router = NeedsPlanningRouter()
    decision = await router.route(event, input_)
    assert decision.action == "needs_planning"
    assert decision.proposed_tasks == []

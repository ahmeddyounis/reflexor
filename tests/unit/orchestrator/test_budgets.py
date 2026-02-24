from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from reflexor.domain.errors import BudgetExceeded
from reflexor.orchestrator.budgets import BudgetLimits, BudgetTracker


@dataclass(slots=True)
class _FakeClock:
    now: int = 0
    monotonic: int = 0

    def now_ms(self) -> int:
        return self.now

    def monotonic_ms(self) -> int:
        return self.monotonic

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(0)
        delta_ms = int(float(seconds) * 1000)
        self.now += delta_ms
        self.monotonic += delta_ms


def test_exceeding_max_tasks_raises_budget_exceeded() -> None:
    clock = _FakeClock()
    tracker = BudgetTracker(limits=BudgetLimits(max_tasks_per_run=2), clock=clock)

    tracker.accept_tasks(1, source="reflex")
    tracker.accept_tasks(1, source="plan")

    with pytest.raises(BudgetExceeded) as excinfo:
        tracker.accept_tasks(1, source="plan")

    exc = excinfo.value
    assert exc.context["budget"] == "max_tasks_per_run"
    assert exc.context["limit"] == 2
    assert exc.context["current"] == 2
    assert exc.context["requested"] == 1
    assert exc.context["would_be"] == 3
    assert exc.context["source"] == "plan"


def test_exceeding_max_tool_calls_raises_budget_exceeded() -> None:
    clock = _FakeClock()
    tracker = BudgetTracker(limits=BudgetLimits(max_tool_calls_per_run=1), clock=clock)

    tracker.accept_tool_calls(1, source="reflex")

    with pytest.raises(BudgetExceeded) as excinfo:
        tracker.accept_tool_calls(1, source="plan")

    exc = excinfo.value
    assert exc.context["budget"] == "max_tool_calls_per_run"
    assert exc.context["limit"] == 1
    assert exc.context["current"] == 1
    assert exc.context["requested"] == 1
    assert exc.context["would_be"] == 2


def test_wall_time_deadline_is_enforced_with_injected_clock() -> None:
    clock = _FakeClock(monotonic=0)
    tracker = BudgetTracker(limits=BudgetLimits(max_wall_time_s=1.0), clock=clock)

    clock.monotonic = tracker.started_monotonic_ms + 999
    tracker.check_wall_time()

    clock.monotonic = tracker.started_monotonic_ms + 1000
    with pytest.raises(BudgetExceeded) as excinfo:
        tracker.check_wall_time()

    exc = excinfo.value
    assert exc.context["budget"] == "max_wall_time_s"
    assert exc.context["deadline_monotonic_ms"] == tracker.deadline_monotonic_ms

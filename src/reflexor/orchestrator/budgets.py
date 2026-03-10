"""Budget primitives for orchestrated runs.

Budgets are intended to bound resource usage (time, number of tasks, tool calls, etc.) at the
application/orchestrator layer.

Clean Architecture:
- Orchestrator is application-layer code.
- This module may depend on `reflexor.domain` and stdlib.
- Forbidden: FastAPI/SQLAlchemy/httpx/worker/API/CLI imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from reflexor.domain.errors import BudgetExceeded
from reflexor.orchestrator.clock import Clock


@dataclass(frozen=True, slots=True)
class BudgetLimits:
    """Budget caps for orchestrated runs.

    A value of `None` means "unbounded" for that dimension.
    """

    max_tasks_per_run: int | None = None
    max_tool_calls_per_run: int | None = None
    max_tokens_per_run: int | None = None
    max_wall_time_s: float | None = None
    max_events_per_planning_cycle: int | None = None
    max_backlog_events: int | None = None

    def __post_init__(self) -> None:
        _validate_optional_positive_int(self.max_tasks_per_run, field_name="max_tasks_per_run")
        _validate_optional_positive_int(
            self.max_tool_calls_per_run, field_name="max_tool_calls_per_run"
        )
        _validate_optional_positive_int(self.max_tokens_per_run, field_name="max_tokens_per_run")
        _validate_optional_positive_int(
            self.max_events_per_planning_cycle, field_name="max_events_per_planning_cycle"
        )
        _validate_optional_positive_int(self.max_backlog_events, field_name="max_backlog_events")
        if self.max_wall_time_s is not None:
            max_wall_time_s = float(self.max_wall_time_s)
            if max_wall_time_s <= 0:
                raise ValueError("max_wall_time_s must be > 0")


def _validate_optional_positive_int(value: int | None, *, field_name: str) -> None:
    if value is None:
        return
    if int(value) <= 0:
        raise ValueError(f"{field_name} must be > 0")


def budget_exceeded_to_audit_dict(exc: BudgetExceeded) -> dict[str, object]:
    """Convert a `BudgetExceeded` into a JSON-safe audit record."""

    return {"type": "budget_exceeded", "message": exc.message, "context": dict(exc.context)}


@dataclass(slots=True)
class BudgetTracker:
    """Stateful budget tracker for a single run.

    The tracker enforces caps by raising `BudgetExceeded` with structured metadata.
    Time-based enforcement uses the injected clock's monotonic time to avoid wall-clock jumps.
    """

    limits: BudgetLimits
    clock: Clock

    tasks_accepted: int = 0
    tool_calls_accepted: int = 0
    events_seen_in_planning_cycle: int = 0
    backlog_events_seen: int = 0

    started_monotonic_ms: int = 0
    deadline_monotonic_ms: int | None = None

    def __post_init__(self) -> None:
        self.started_monotonic_ms = int(self.clock.monotonic_ms())
        self.deadline_monotonic_ms = _compute_deadline_ms(
            self.started_monotonic_ms, self.limits.max_wall_time_s
        )

    def snapshot(self) -> dict[str, object]:
        return {
            "tasks_accepted": self.tasks_accepted,
            "tool_calls_accepted": self.tool_calls_accepted,
            "events_seen_in_planning_cycle": self.events_seen_in_planning_cycle,
            "backlog_events_seen": self.backlog_events_seen,
            "started_monotonic_ms": self.started_monotonic_ms,
            "deadline_monotonic_ms": self.deadline_monotonic_ms,
            "limits": {
                "max_tasks_per_run": self.limits.max_tasks_per_run,
                "max_tool_calls_per_run": self.limits.max_tool_calls_per_run,
                "max_tokens_per_run": self.limits.max_tokens_per_run,
                "max_wall_time_s": self.limits.max_wall_time_s,
                "max_events_per_planning_cycle": self.limits.max_events_per_planning_cycle,
                "max_backlog_events": self.limits.max_backlog_events,
            },
        }

    def check_wall_time(self, *, now_monotonic_ms: int | None = None) -> None:
        deadline = self.deadline_monotonic_ms
        if deadline is None:
            return

        now_ms = int(self.clock.monotonic_ms() if now_monotonic_ms is None else now_monotonic_ms)
        if now_ms < deadline:
            return

        raise BudgetExceeded(
            "wall-time budget exceeded",
            budget="max_wall_time_s",
            context={
                "now_monotonic_ms": now_ms,
                "deadline_monotonic_ms": deadline,
                "started_monotonic_ms": self.started_monotonic_ms,
                "elapsed_ms": now_ms - self.started_monotonic_ms,
                "limit_ms": deadline - self.started_monotonic_ms,
            },
        )

    def accept_tasks(self, count: int, *, source: str | None = None) -> None:
        self._accept_count(
            kind="tasks",
            budget_name="max_tasks_per_run",
            current=self.tasks_accepted,
            limit=self.limits.max_tasks_per_run,
            count=count,
            source=source,
        )
        self.tasks_accepted += int(count)

    def accept_tool_calls(self, count: int, *, source: str | None = None) -> None:
        self._accept_count(
            kind="tool_calls",
            budget_name="max_tool_calls_per_run",
            current=self.tool_calls_accepted,
            limit=self.limits.max_tool_calls_per_run,
            count=count,
            source=source,
        )
        self.tool_calls_accepted += int(count)

    def reset_planning_cycle(self) -> None:
        self.events_seen_in_planning_cycle = 0

    def observe_planning_events(self, count: int, *, source: str | None = None) -> None:
        self._accept_count(
            kind="planning_events",
            budget_name="max_events_per_planning_cycle",
            current=self.events_seen_in_planning_cycle,
            limit=self.limits.max_events_per_planning_cycle,
            count=count,
            source=source,
        )
        self.events_seen_in_planning_cycle += int(count)

    def observe_backlog_events(self, count: int, *, source: str | None = None) -> None:
        self._accept_count(
            kind="backlog_events",
            budget_name="max_backlog_events",
            current=self.backlog_events_seen,
            limit=self.limits.max_backlog_events,
            count=count,
            source=source,
        )
        self.backlog_events_seen += int(count)

    def _accept_count(
        self,
        *,
        kind: str,
        budget_name: str,
        current: int,
        limit: int | None,
        count: int,
        source: str | None,
    ) -> None:
        self.check_wall_time()
        if int(count) <= 0:
            raise ValueError("count must be > 0")

        if limit is None:
            return

        next_total = current + int(count)
        if next_total <= int(limit):
            return

        context: dict[str, Any] = {
            "kind": kind,
            "limit": int(limit),
            "current": int(current),
            "requested": int(count),
            "would_be": int(next_total),
        }
        if source is not None:
            context["source"] = source

        raise BudgetExceeded(
            f"{kind} budget exceeded",
            budget=budget_name,
            context=context,
        )


def _compute_deadline_ms(start_monotonic_ms: int, max_wall_time_s: float | None) -> int | None:
    if max_wall_time_s is None:
        return None
    limit_ms = int(float(max_wall_time_s) * 1000)
    return int(start_monotonic_ms) + limit_ms


__all__ = [
    "BudgetLimits",
    "BudgetTracker",
    "budget_exceeded_to_audit_dict",
]

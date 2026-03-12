"""Orchestration engine entrypoint (composition-friendly application service)."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field

from reflexor.domain.models import Task
from reflexor.domain.models_event import Event
from reflexor.observability.metrics import ReflexorMetrics
from reflexor.orchestrator.budgets import BudgetLimits
from reflexor.orchestrator.clock import Clock, SystemClock
from reflexor.orchestrator.engine.backlog import (
    drain_backlog as _drain_backlog,
)
from reflexor.orchestrator.engine.backlog import (
    enqueue_backlog_event as _enqueue_backlog_event,
)
from reflexor.orchestrator.engine.planning import run_planning_once as _run_planning_once
from reflexor.orchestrator.engine.queueing import enqueue_tasks as _enqueue_tasks
from reflexor.orchestrator.engine.reflex import handle_event as _handle_event
from reflexor.orchestrator.engine.types import PlanningTrigger
from reflexor.orchestrator.event_suppression import EventSuppressor
from reflexor.orchestrator.interfaces import Planner, ReflexRouter
from reflexor.orchestrator.persistence import OrchestratorPersistence
from reflexor.orchestrator.queue import Queue
from reflexor.orchestrator.sinks import NoopRunPacketSink, RunPacketSink
from reflexor.orchestrator.triggers import DebouncedTrigger, PeriodicTicker
from reflexor.tools.registry import ToolRegistry


@dataclass(frozen=True, slots=True)
class EventHandleOutcome:
    event_id: str
    run_id: str | None
    duplicate: bool


@dataclass(slots=True)
class OrchestratorEngine:
    """Composition-friendly orchestrator engine.

    This engine performs reflex routing and queues tasks, but does not execute tools.
    """

    reflex_router: ReflexRouter
    planner: Planner
    tool_registry: ToolRegistry
    queue: Queue
    run_sink: RunPacketSink = field(default_factory=NoopRunPacketSink)
    persistence: OrchestratorPersistence | None = None
    event_suppressor: EventSuppressor | None = None
    limits: BudgetLimits = field(default_factory=BudgetLimits)
    clock: Clock = field(default_factory=SystemClock)
    metrics: ReflexorMetrics | None = None
    planner_debounce_s: float = 0.25
    planner_interval_s: float = 30.0
    enabled_scopes: tuple[str, ...] = ()
    approval_required_scopes: tuple[str, ...] = ()

    _backlog: deque[Event] = field(default_factory=deque, init=False)
    _backlog_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    _planning_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    _planning_debouncer: DebouncedTrigger | None = field(default=None, init=False)
    _planning_ticker: PeriodicTicker | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        if float(self.planner_debounce_s) <= 0:
            raise ValueError("planner_debounce_s must be > 0")
        if float(self.planner_interval_s) <= 0:
            raise ValueError("planner_interval_s must be > 0")

        async def plan_from_event() -> None:
            await self.run_planning_once(trigger="event")

        async def plan_from_tick() -> None:
            await self.run_planning_once(trigger="tick")

        self._planning_debouncer = DebouncedTrigger(
            callback=plan_from_event,
            clock=self.clock,
            debounce_s=self.planner_debounce_s,
        )
        self._planning_ticker = PeriodicTicker(
            callback=plan_from_tick,
            clock=self.clock,
            planner_interval_s=self.planner_interval_s,
        )

    def start(self) -> None:
        """Start background planning triggers (debounce + periodic tick)."""

        if self._planning_debouncer is not None:
            self._planning_debouncer.start()
        if self._planning_ticker is not None:
            self._planning_ticker.start()

    async def aclose(self) -> None:
        """Shut down background planning triggers."""

        if self._planning_debouncer is not None:
            await self._planning_debouncer.aclose()
        if self._planning_ticker is not None:
            await self._planning_ticker.aclose()

    async def submit_event(self, event: Event) -> EventHandleOutcome:
        """Handle a single event and return the resulting ingestion outcome."""

        return await _handle_event(self, event)

    async def handle_event(self, event: Event) -> str:
        """Handle a single event and return the created `run_id`."""

        outcome = await self.submit_event(event)
        if outcome.run_id is None:
            raise RuntimeError(
                "event was deduplicated before the original run packet was available"
            )
        return outcome.run_id

    async def run_planning_once(self, *, trigger: PlanningTrigger) -> str:
        """Run a single planning cycle."""

        return await _run_planning_once(self, trigger=trigger)

    async def _enqueue_tasks(
        self,
        tasks: Sequence[Task],
        *,
        reason: str,
        source: str,
        trigger: PlanningTrigger | None = None,
        first_enqueue_started_s: float | None = None,
    ) -> list[str]:
        return await _enqueue_tasks(
            self,
            tasks,
            reason=reason,
            source=source,
            trigger=trigger,
            first_enqueue_started_s=first_enqueue_started_s,
        )

    async def _enqueue_backlog_event(self, event: Event) -> None:
        await _enqueue_backlog_event(self, event)

    async def drain_backlog(self, *, max_items: int | None = None) -> list[Event]:
        return await _drain_backlog(self, max_items=max_items)


__all__ = ["EventHandleOutcome", "OrchestratorEngine"]

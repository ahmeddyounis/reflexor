"""Orchestration engine scaffolding.

The orchestrator engine is responsible for coordinating reflex decisions, planning, queueing, and
execution while keeping dependencies pointed inward (Clean Architecture).

Clean Architecture:
- Orchestrator is application-layer code.
- Engine code may depend on `reflexor.domain`, `reflexor.config`, queue interface/contracts, and
  tool boundary types/registries.
- Forbidden: FastAPI/SQLAlchemy/httpx/worker/API/CLI imports.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol
from uuid import uuid4

from reflexor.domain.errors import BudgetExceeded
from reflexor.domain.models import Task
from reflexor.domain.models_event import Event
from reflexor.domain.models_run_packet import RunPacket
from reflexor.observability.context import correlation_context, get_correlation_ids
from reflexor.orchestrator.budgets import BudgetLimits, BudgetTracker, budget_exceeded_to_audit_dict
from reflexor.orchestrator.clock import Clock, SystemClock
from reflexor.orchestrator.interfaces import Planner, ReflexRouter
from reflexor.orchestrator.plans import LimitsSnapshot, PlanningInput
from reflexor.orchestrator.queue import Queue, TaskEnvelope
from reflexor.orchestrator.triggers import DebouncedTrigger, PeriodicTicker
from reflexor.orchestrator.validation import PlanValidationError, PlanValidator
from reflexor.tools.registry import ToolRegistry

PlanningTrigger = Literal["tick", "event"]


class RunPacketSink(Protocol):
    async def write(self, packet: RunPacket) -> None: ...


class NoopRunPacketSink:
    async def write(self, packet: RunPacket) -> None:
        _ = packet


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
    limits: BudgetLimits = field(default_factory=BudgetLimits)
    clock: Clock = SystemClock()
    planner_debounce_s: float = 0.25
    planner_interval_s: float = 30.0

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

    async def handle_event(self, event: Event) -> str:
        """Handle a single event and return the created `run_id`."""

        run_id = str(uuid4())
        tracker = BudgetTracker(limits=self.limits, clock=self.clock)
        validator = PlanValidator(registry=self.tool_registry)

        reflex_decision_dict: dict[str, object] = {}
        tasks: list[Task] = []
        policy_decisions: list[dict[str, object]] = []

        with correlation_context(event_id=event.event_id, run_id=run_id):
            try:
                planning_input = PlanningInput(
                    trigger="event", events=[event], now_ms=self.clock.now_ms()
                )
                decision = await self.reflex_router.route(event, planning_input)
                reflex_decision_dict = decision.model_dump(mode="json")

                if decision.action == "fast_tasks":
                    proposed_tasks = list(decision.proposed_tasks)
                    tracker.accept_tasks(len(proposed_tasks), source="reflex")
                    tracker.accept_tool_calls(len(proposed_tasks), source="reflex")

                    tasks = validator.build_tasks(
                        proposed_tasks,
                        run_id=run_id,
                        seed_source="reflex",
                        event_id=event.event_id,
                    )
                    await self._enqueue_tasks(
                        tasks,
                        reason=decision.reason,
                        source="reflex",
                        trigger="event",
                    )
                elif decision.action == "needs_planning":
                    await self._enqueue_backlog_event(event)
                    if self._planning_debouncer is not None:
                        self._planning_debouncer.trigger()
                elif decision.action == "drop":
                    pass
                else:  # pragma: no cover
                    raise AssertionError(f"unknown reflex decision action: {decision.action!r}")
            except BudgetExceeded as exc:
                policy_decisions.append(budget_exceeded_to_audit_dict(exc))
            except PlanValidationError as exc:
                policy_decisions.append(
                    {
                        "type": "plan_validation_error",
                        "message": str(exc),
                    }
                )

            run_packet = RunPacket(
                run_id=run_id,
                event=event,
                reflex_decision=reflex_decision_dict,
                tasks=tasks,
                policy_decisions=policy_decisions,
            )
            await self.run_sink.write(run_packet)
        return run_id

    async def run_planning_once(self, *, trigger: PlanningTrigger) -> str:
        """Run a single planning cycle.

        This method snapshots events from the backlog, calls the planner, validates the resulting
        plan into domain tasks, and enqueues them. Backlog events are removed only after successful
        plan validation and queueing.
        """

        planning_run_id = str(uuid4())
        tracker = BudgetTracker(limits=self.limits, clock=self.clock)
        validator = PlanValidator(registry=self.tool_registry)

        plan_dict: dict[str, object] = {}
        tasks: list[Task] = []
        policy_decisions: list[dict[str, object]] = []

        async with self._planning_lock:
            async with self._backlog_lock:
                backlog_before = len(self._backlog)
                max_events = self.limits.max_events_per_planning_cycle
                if max_events is None:
                    max_events = backlog_before
                else:
                    max_events = min(int(max_events), backlog_before)

                selected_events: list[Event] = []
                for idx, item in enumerate(self._backlog):
                    if idx >= max_events:
                        break
                    selected_events.append(item)

            now_ms = int(self.clock.now_ms())
            synthetic_event = Event(
                type="planning_cycle",
                source="orchestrator",
                received_at_ms=now_ms,
                payload={
                    "trigger": trigger,
                    "selected_events": len(selected_events),
                    "backlog_before": backlog_before,
                },
            )

            with correlation_context(event_id=synthetic_event.event_id, run_id=planning_run_id):
                try:
                    effective_trigger: PlanningTrigger = trigger
                    if effective_trigger == "event" and not selected_events:
                        effective_trigger = "tick"

                    planning_input = PlanningInput(
                        trigger=effective_trigger,
                        events=selected_events,
                        limits=LimitsSnapshot(
                            max_tasks=self.limits.max_tasks_per_run,
                            max_tool_calls=self.limits.max_tool_calls_per_run,
                            max_runtime_s=self.limits.max_wall_time_s,
                        ),
                        now_ms=now_ms,
                    )
                    plan = await self.planner.plan(planning_input)
                    plan_dict = plan.model_dump(mode="json")

                    proposed_tasks = list(plan.tasks)
                    if selected_events:
                        tracker.observe_planning_events(len(selected_events), source="planner")
                    if proposed_tasks:
                        tracker.accept_tasks(len(proposed_tasks), source="planner")
                        tracker.accept_tool_calls(len(proposed_tasks), source="planner")

                    tasks = validator.build_tasks(
                        proposed_tasks,
                        run_id=planning_run_id,
                        seed_source="planning",
                    )
                    await self._enqueue_tasks(
                        tasks,
                        reason=plan.summary,
                        source="planner",
                        trigger=effective_trigger,
                    )

                    if selected_events:
                        async with self._backlog_lock:
                            for _ in range(len(selected_events)):
                                if not self._backlog:
                                    break
                                self._backlog.popleft()
                except BudgetExceeded as exc:
                    policy_decisions.append(budget_exceeded_to_audit_dict(exc))
                except PlanValidationError as exc:
                    policy_decisions.append(
                        {
                            "type": "plan_validation_error",
                            "message": str(exc),
                        }
                    )
                except Exception as exc:  # pragma: no cover
                    policy_decisions.append(
                        {
                            "type": "planning_error",
                            "message": str(exc),
                        }
                    )

                run_packet = RunPacket(
                    run_id=planning_run_id,
                    event=synthetic_event,
                    plan=plan_dict,
                    tasks=tasks,
                    policy_decisions=policy_decisions,
                )
                await self.run_sink.write(run_packet)

        return planning_run_id

    async def _enqueue_tasks(
        self,
        tasks: Sequence[Task],
        *,
        reason: str,
        source: str,
        trigger: PlanningTrigger | None = None,
    ) -> None:
        now_ms = int(self.clock.now_ms())
        for task in tasks:
            tool_call = task.tool_call
            if tool_call is None:
                raise PlanValidationError("task.tool_call is required for queueing")

            with correlation_context(task_id=task.task_id, tool_call_id=tool_call.tool_call_id):
                envelope = TaskEnvelope(
                    task_id=task.task_id,
                    run_id=task.run_id,
                    attempt=task.attempts,
                    created_at_ms=now_ms,
                    available_at_ms=now_ms,
                    correlation_ids=get_correlation_ids(),
                    trace={"reason": reason, "source": source, "trigger": trigger},
                    payload={
                        "tool_call_id": tool_call.tool_call_id,
                        "tool_name": tool_call.tool_name,
                        "permission_scope": tool_call.permission_scope,
                        "idempotency_key": tool_call.idempotency_key,
                    },
                )
                await self.queue.enqueue(envelope)

    async def _enqueue_backlog_event(self, event: Event) -> None:
        async with self._backlog_lock:
            limit = self.limits.max_backlog_events
            if limit is not None and len(self._backlog) + 1 > limit:
                raise BudgetExceeded(
                    "backlog budget exceeded",
                    budget="max_backlog_events",
                    context={
                        "limit": limit,
                        "current": len(self._backlog),
                        "would_be": len(self._backlog) + 1,
                    },
                )
            self._backlog.append(event)

    async def drain_backlog(self, *, max_items: int | None = None) -> list[Event]:
        """Remove and return up to `max_items` events from the backlog."""

        async with self._backlog_lock:
            if max_items is None:
                items = list(self._backlog)
                self._backlog.clear()
                return items

            if max_items <= 0:
                return []

            drained: list[Event] = []
            for _ in range(min(max_items, len(self._backlog))):
                drained.append(self._backlog.popleft())
            return drained


__all__ = ["NoopRunPacketSink", "OrchestratorEngine", "RunPacketSink"]

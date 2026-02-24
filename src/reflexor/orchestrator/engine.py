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
from typing import Protocol
from uuid import uuid4

from reflexor.domain.errors import BudgetExceeded
from reflexor.domain.models import Task
from reflexor.domain.models_event import Event
from reflexor.domain.models_run_packet import RunPacket
from reflexor.observability.context import correlation_context, get_correlation_ids
from reflexor.orchestrator.budgets import BudgetLimits, BudgetTracker, budget_exceeded_to_audit_dict
from reflexor.orchestrator.clock import Clock, SystemClock
from reflexor.orchestrator.interfaces import Planner, ReflexRouter
from reflexor.orchestrator.plans import PlanningInput
from reflexor.orchestrator.queue import Queue, TaskEnvelope
from reflexor.orchestrator.validation import PlanValidationError, PlanValidator
from reflexor.tools.registry import ToolRegistry


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

    _backlog: deque[Event] = field(default_factory=deque, init=False)
    _backlog_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    _planning_requested: asyncio.Event = field(default_factory=asyncio.Event, init=False)

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
                    await self._enqueue_tasks(tasks, reason=decision.reason)
                elif decision.action == "needs_planning":
                    await self._enqueue_backlog_event(event)
                    self._planning_requested.set()
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

    async def _enqueue_tasks(self, tasks: Sequence[Task], *, reason: str) -> None:
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
                    trace={"reason": reason, "source": "reflex"},
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

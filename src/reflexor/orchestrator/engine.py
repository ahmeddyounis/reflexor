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

from dataclasses import dataclass

from reflexor.domain.models_event import Event
from reflexor.orchestrator.clock import Clock, SystemClock
from reflexor.orchestrator.interfaces import Planner, ReflexRouter
from reflexor.orchestrator.plans import PlanningInput
from reflexor.orchestrator.queue import Queue


@dataclass(slots=True)
class OrchestratorEngine:
    """Minimal composition-friendly engine skeleton (no behavior yet)."""

    queue: Queue
    reflex_router: ReflexRouter
    planner: Planner
    clock: Clock = SystemClock()

    async def handle_event(self, event: Event) -> dict[str, object]:
        """Handle an event and return a JSON-safe summary (placeholder)."""

        planning_input = PlanningInput(trigger="event", events=[event], now_ms=self.clock.now_ms())
        decision = await self.reflex_router.route(event, planning_input)
        plan = await self.planner.plan(planning_input)
        return {"decision": decision.model_dump(mode="json"), "planned_tasks": len(plan.tasks)}


__all__ = ["OrchestratorEngine"]

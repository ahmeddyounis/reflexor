"""Narrow interfaces for orchestrator components.

This module defines ISP-friendly interfaces (Protocols) for orchestrator building blocks so the
engine can be composed without depending on concrete implementations.

Clean Architecture:
- Orchestrator is application-layer code.
- This module may depend on `reflexor.domain`, `reflexor.config`, queue interface, and tool
  boundary contracts.
- Forbidden: FastAPI/SQLAlchemy/httpx/worker/API/CLI imports.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from reflexor.domain.models_event import Event
from reflexor.orchestrator.plans import Plan, PlanningInput, ReflexDecision


class Planner(Protocol):
    """Planner: derive a plan from planning input."""

    async def plan(self, input: PlanningInput) -> Plan: ...


class ReflexRouter(Protocol):
    """Reflex router: decide next action given an event and context."""

    async def route(self, event: Event, ctx: PlanningInput) -> ReflexDecision: ...


class NoOpPlanner:
    """Planner stub that emits an empty plan."""

    async def plan(self, input: PlanningInput) -> Plan:
        _ = input
        return Plan(summary="noop", tasks=[], metadata={})


class NeedsPlanningRouter:
    """Reflex router stub that always requests planning."""

    rule_id: str = "needs_planning_stub"

    async def route(self, event: Event, ctx: PlanningInput) -> ReflexDecision:
        _ = event
        _ = ctx
        return ReflexDecision(action="needs_planning", reason="stub", proposed_tasks=[])


if TYPE_CHECKING:
    _planner: Planner = NoOpPlanner()
    _router: ReflexRouter = NeedsPlanningRouter()


__all__ = ["NeedsPlanningRouter", "NoOpPlanner", "Planner", "ReflexRouter"]

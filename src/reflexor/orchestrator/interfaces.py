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
from reflexor.orchestrator.plans import BudgetAssertions, Plan, PlanningInput, ReflexDecision


class Planner(Protocol):
    """Planner: derive a plan from planning input."""

    async def plan(self, input: PlanningInput) -> Plan: ...


class ReflexRouter(Protocol):
    """Reflex router: decide next action given an event and context."""

    async def route(self, event: Event, ctx: PlanningInput) -> ReflexDecision: ...


class ReflexClassifier(Protocol):
    """Optional constrained classifier used as a fallback after deterministic rules."""

    async def classify(self, event: Event, ctx: PlanningInput) -> ReflexDecision | None: ...


class NoOpPlanner:
    """Planner stub that emits an empty plan."""

    async def plan(self, input: PlanningInput) -> Plan:
        return Plan(
            summary="noop",
            tasks=[],
            budget_assertions=BudgetAssertions(
                max_tool_calls=int(input.limits.max_tool_calls or 1),
                max_runtime_s=float(input.limits.max_runtime_s or 1.0),
                max_tokens=int(input.limits.max_tokens or 1),
            ),
            metadata={},
        )


class NeedsPlanningRouter:
    """Reflex router stub that always requests planning."""

    rule_id: str = "needs_planning_stub"

    async def route(self, event: Event, ctx: PlanningInput) -> ReflexDecision:
        _ = event
        _ = ctx
        return ReflexDecision(action="needs_planning", reason="stub", proposed_tasks=[])


class NoOpReflexClassifier:
    """Classifier stub that never overrides deterministic routing."""

    async def classify(self, event: Event, ctx: PlanningInput) -> ReflexDecision | None:
        _ = event
        _ = ctx
        return None


if TYPE_CHECKING:
    _planner: Planner = NoOpPlanner()
    _router: ReflexRouter = NeedsPlanningRouter()
    _classifier: ReflexClassifier = NoOpReflexClassifier()


__all__ = [
    "NeedsPlanningRouter",
    "NoOpPlanner",
    "NoOpReflexClassifier",
    "Planner",
    "ReflexClassifier",
    "ReflexRouter",
]

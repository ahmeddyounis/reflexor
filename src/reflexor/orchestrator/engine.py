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
from uuid import uuid4

from reflexor.domain.errors import BudgetExceeded
from reflexor.domain.models_event import Event
from reflexor.domain.models_run_packet import RunPacket
from reflexor.orchestrator.budgets import BudgetLimits, BudgetTracker, budget_exceeded_to_audit_dict
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
    budget_limits: BudgetLimits = BudgetLimits()

    async def handle_event(self, event: Event) -> dict[str, object]:
        """Handle an event and return a JSON-safe summary (placeholder)."""

        run_id = str(uuid4())
        planning_input = PlanningInput(trigger="event", events=[event], now_ms=self.clock.now_ms())
        tracker = BudgetTracker(limits=self.budget_limits, clock=self.clock)

        decision_dict: dict[str, object] = {}
        plan_dict: dict[str, object] = {}
        policy_decisions: list[dict[str, object]] = []
        planned_tasks = 0

        try:
            tracker.observe_planning_events(len(planning_input.events), source="planning_input")
            tracker.check_wall_time()

            decision = await self.reflex_router.route(event, planning_input)
            decision_dict = decision.model_dump(mode="json")
            tracker.accept_tasks(len(decision.proposed_tasks), source="reflex")
            tracker.accept_tool_calls(len(decision.proposed_tasks), source="reflex")

            plan = await self.planner.plan(planning_input)
            planned_tasks = len(plan.tasks)
            plan_dict = plan.model_dump(mode="json")
            tracker.accept_tasks(len(plan.tasks), source="planner")
            tracker.accept_tool_calls(len(plan.tasks), source="planner")
        except BudgetExceeded as exc:
            policy_decisions.append(budget_exceeded_to_audit_dict(exc))

        run_packet = RunPacket(
            run_id=run_id,
            event=event,
            reflex_decision=decision_dict,
            plan=plan_dict,
            policy_decisions=policy_decisions,
        )

        return {
            "run_id": run_id,
            "decision": decision_dict,
            "planned_tasks": planned_tasks,
            "policy_decisions": policy_decisions,
            "run_packet": run_packet.model_dump(mode="json"),
        }


__all__ = ["OrchestratorEngine"]

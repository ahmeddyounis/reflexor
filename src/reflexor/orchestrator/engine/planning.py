from __future__ import annotations

import time
from typing import TYPE_CHECKING
from uuid import uuid4

from reflexor.domain.enums import TaskStatus
from reflexor.domain.errors import BudgetExceeded
from reflexor.domain.models import Task
from reflexor.domain.models_event import Event
from reflexor.domain.models_run_packet import RunPacket
from reflexor.observability.context import correlation_context
from reflexor.orchestrator.budgets import BudgetTracker, budget_exceeded_to_audit_dict
from reflexor.orchestrator.engine.types import PlanningTrigger
from reflexor.orchestrator.plans import LimitsSnapshot, Plan, PlanningInput
from reflexor.orchestrator.validation import PlanValidationError, PlanValidator
from reflexor.storage.ports import RunRecord

if TYPE_CHECKING:
    from reflexor.orchestrator.engine.core import OrchestratorEngine


def _validate_plan_budget_assertions(*, engine: OrchestratorEngine, plan: Plan) -> None:
    assertions = plan.budget_assertions

    if (
        assertions.max_tasks is not None
        and engine.limits.max_tasks_per_run is not None
        and assertions.max_tasks > int(engine.limits.max_tasks_per_run)
    ):
        raise BudgetExceeded(
            "planner budget assertion exceeds configured max_tasks_per_run",
            budget="max_tasks_per_run",
            context={
                "asserted_max_tasks": assertions.max_tasks,
                "configured_max_tasks_per_run": int(engine.limits.max_tasks_per_run),
            },
        )
    if (
        assertions.max_tool_calls is not None
        and engine.limits.max_tool_calls_per_run is not None
        and assertions.max_tool_calls > int(engine.limits.max_tool_calls_per_run)
    ):
        raise BudgetExceeded(
            "planner budget assertion exceeds configured max_tool_calls_per_run",
            budget="max_tool_calls_per_run",
            context={
                "asserted_max_tool_calls": assertions.max_tool_calls,
                "configured_max_tool_calls_per_run": int(engine.limits.max_tool_calls_per_run),
            },
        )
    if (
        assertions.max_runtime_s is not None
        and engine.limits.max_wall_time_s is not None
        and assertions.max_runtime_s > float(engine.limits.max_wall_time_s)
    ):
        raise BudgetExceeded(
            "planner budget assertion exceeds configured max_run_wall_time_s",
            budget="max_run_wall_time_s",
            context={
                "asserted_max_runtime_s": assertions.max_runtime_s,
                "configured_max_run_wall_time_s": float(engine.limits.max_wall_time_s),
            },
        )


async def run_planning_once(engine: OrchestratorEngine, *, trigger: PlanningTrigger) -> str:
    """Run a single planning cycle.

    This snapshots events from the backlog, calls the planner, validates the resulting plan into
    domain tasks, and enqueues them. Backlog events are removed only after successful plan
    validation and queueing.
    """

    started_perf_s = time.perf_counter()
    planning_run_id = str(uuid4())
    tracker = BudgetTracker(limits=engine.limits, clock=engine.clock)
    validator = PlanValidator(
        registry=engine.tool_registry,
        approval_required_scopes=engine.approval_required_scopes,
    )

    plan_dict: dict[str, object] = {}
    tasks: list[Task] = []
    policy_decisions: list[dict[str, object]] = []
    enqueued_task_ids: list[str] = []

    try:
        async with engine._planning_lock:
            async with engine._backlog_lock:
                backlog_before = len(engine._backlog)
                max_events = engine.limits.max_events_per_planning_cycle
                if max_events is None:
                    max_events = backlog_before
                else:
                    max_events = min(int(max_events), backlog_before)

                selected_events: list[Event] = []
                for idx, item in enumerate(engine._backlog):
                    if idx >= max_events:
                        break
                    selected_events.append(item)

            now_ms = int(engine.clock.now_ms())
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
            persisted_event = synthetic_event

            if engine.persistence is not None:
                persisted_event = await engine.persistence.persist_event_and_run(
                    event=synthetic_event,
                    run_record=RunRecord(
                        run_id=planning_run_id,
                        parent_run_id=None,
                        created_at_ms=now_ms,
                        started_at_ms=None,
                        completed_at_ms=None,
                    ),
                )

            with correlation_context(event_id=persisted_event.event_id, run_id=planning_run_id):
                try:
                    effective_trigger: PlanningTrigger = trigger
                    if effective_trigger == "event" and not selected_events:
                        effective_trigger = "tick"

                    planning_input = PlanningInput(
                        trigger=effective_trigger,
                        events=selected_events,
                        limits=LimitsSnapshot(
                            max_tasks=engine.limits.max_tasks_per_run,
                            max_tool_calls=engine.limits.max_tool_calls_per_run,
                            max_runtime_s=engine.limits.max_wall_time_s,
                        ),
                        now_ms=now_ms,
                    )
                    plan = await engine.planner.plan(planning_input)
                    _validate_plan_budget_assertions(engine=engine, plan=plan)
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
                    if engine.persistence is not None:
                        await engine.persistence.persist_tasks_and_tool_calls(tasks)

                    enqueued_task_ids = await engine._enqueue_tasks(
                        tasks,
                        reason=plan.summary,
                        source="planner",
                        trigger=effective_trigger,
                    )
                    if enqueued_task_ids:
                        enqueued_set = set(enqueued_task_ids)
                        tasks = [
                            (
                                task.model_copy(update={"status": TaskStatus.QUEUED}, deep=True)
                                if task.task_id in enqueued_set
                                else task
                            )
                            for task in tasks
                        ]

                    if selected_events:
                        async with engine._backlog_lock:
                            for _ in range(len(selected_events)):
                                if not engine._backlog:
                                    break
                                engine._backlog.popleft()
                except BudgetExceeded as exc:
                    if engine.metrics is not None:
                        engine.metrics.orchestrator_rejections_total.labels(reason="budget").inc()
                    policy_decisions.append(budget_exceeded_to_audit_dict(exc))
                except PlanValidationError as exc:
                    if engine.metrics is not None:
                        engine.metrics.orchestrator_rejections_total.labels(
                            reason="validation"
                        ).inc()
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
                    event=persisted_event,
                    plan=plan_dict,
                    tasks=tasks,
                    policy_decisions=policy_decisions,
                    created_at_ms=now_ms,
                )
                await engine.run_sink.emit(run_packet)
                if engine.persistence is not None:
                    await engine.persistence.finalize_run(
                        run_packet, enqueued_task_ids=enqueued_task_ids
                    )

        return planning_run_id
    finally:
        if engine.metrics is not None:
            engine.metrics.planner_latency_seconds.observe(time.perf_counter() - started_perf_s)

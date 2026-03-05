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
from reflexor.orchestrator.plans import PlanningInput
from reflexor.orchestrator.validation import PlanValidationError, PlanValidator
from reflexor.storage.ports import RunRecord

if TYPE_CHECKING:
    from reflexor.orchestrator.engine.core import OrchestratorEngine


async def handle_event(engine: OrchestratorEngine, event: Event) -> str:
    """Handle a single event and return the created `run_id`."""

    started_perf_s = time.perf_counter()
    run_id = str(uuid4())
    created_at_ms = int(engine.clock.now_ms())
    persisted_event = event

    if engine.persistence is not None:
        persisted_event = await engine.persistence.persist_event_and_run(
            event=event,
            run_record=RunRecord(
                run_id=run_id,
                parent_run_id=None,
                created_at_ms=created_at_ms,
                started_at_ms=None,
                completed_at_ms=None,
            ),
        )

    tracker = BudgetTracker(limits=engine.limits, clock=engine.clock)
    validator = PlanValidator(registry=engine.tool_registry)

    reflex_decision_dict: dict[str, object] = {}
    tasks: list[Task] = []
    policy_decisions: list[dict[str, object]] = []
    enqueued_task_ids: list[str] = []

    with correlation_context(event_id=persisted_event.event_id, run_id=run_id):
        if engine.metrics is not None:
            engine.metrics.events_received_total.inc()
        try:
            suppressed = False
            if engine.event_suppressor is not None:
                suppression = await engine.event_suppressor.observe(persisted_event)
                suppressed = bool(suppression.suppressed)
                if suppressed:
                    if engine.metrics is not None:
                        engine.metrics.orchestrator_rejections_total.labels(
                            reason="suppressed"
                        ).inc()
                        engine.metrics.suppressed_events_total.inc()
                    record = suppression.record
                    reflex_decision_dict = {
                        "action": "suppressed",
                        "reason": "event_suppression_threshold_exceeded",
                        "suppression": {
                            "signature_hash": record.signature_hash,
                            "signature": record.signature,
                            "count": record.count,
                            "threshold": record.threshold,
                            "window_ms": record.window_ms,
                            "window_start_ms": record.window_start_ms,
                            "suppressed_until_ms": record.suppressed_until_ms,
                            "expires_at_ms": record.expires_at_ms,
                            "resume_required": record.resume_required,
                        },
                    }

            if not suppressed:
                planning_input = PlanningInput(
                    trigger="event", events=[persisted_event], now_ms=engine.clock.now_ms()
                )
                decision = await engine.reflex_router.route(persisted_event, planning_input)
                reflex_decision_dict = decision.model_dump(mode="json")

                if decision.action == "fast_tasks":
                    proposed_tasks = list(decision.proposed_tasks)
                    tracker.accept_tasks(len(proposed_tasks), source="reflex")
                    tracker.accept_tool_calls(len(proposed_tasks), source="reflex")

                    tasks = validator.build_tasks(
                        proposed_tasks,
                        run_id=run_id,
                        seed_source="reflex",
                        event_id=persisted_event.event_id,
                    )
                    if engine.persistence is not None:
                        await engine.persistence.persist_tasks_and_tool_calls(tasks)

                    await engine._enqueue_tasks(
                        tasks,
                        reason=decision.reason,
                        source="reflex",
                        trigger="event",
                        first_enqueue_started_s=started_perf_s,
                    )
                    enqueued_task_ids = [task.task_id for task in tasks]
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
                elif decision.action == "needs_planning":
                    await engine._enqueue_backlog_event(persisted_event)
                    if engine._planning_debouncer is not None:
                        engine._planning_debouncer.trigger()
                elif decision.action == "drop":
                    if engine.metrics is not None:
                        engine.metrics.orchestrator_rejections_total.labels(reason="drop").inc()
                    pass
                else:  # pragma: no cover
                    raise AssertionError(f"unknown reflex decision action: {decision.action!r}")
        except BudgetExceeded as exc:
            if engine.metrics is not None:
                engine.metrics.orchestrator_rejections_total.labels(reason="budget").inc()
            policy_decisions.append(budget_exceeded_to_audit_dict(exc))
        except PlanValidationError as exc:
            if engine.metrics is not None:
                engine.metrics.orchestrator_rejections_total.labels(reason="validation").inc()
            policy_decisions.append(
                {
                    "type": "plan_validation_error",
                    "message": str(exc),
                }
            )
        except Exception as exc:  # pragma: no cover
            policy_decisions.append(
                {
                    "type": "reflex_error",
                    "message": str(exc),
                }
            )

        run_packet = RunPacket(
            run_id=run_id,
            event=persisted_event,
            reflex_decision=reflex_decision_dict,
            tasks=tasks,
            policy_decisions=policy_decisions,
            created_at_ms=created_at_ms,
        )
        await engine.run_sink.emit(run_packet)
        if engine.persistence is not None:
            await engine.persistence.finalize_run(run_packet, enqueued_task_ids=enqueued_task_ids)
    return run_id

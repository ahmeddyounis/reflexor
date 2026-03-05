from __future__ import annotations

import time
from collections.abc import Sequence
from typing import TYPE_CHECKING

from reflexor.domain.models import Task
from reflexor.observability.context import correlation_context, get_correlation_ids
from reflexor.orchestrator.engine.types import PlanningTrigger
from reflexor.orchestrator.queue import TaskEnvelope
from reflexor.orchestrator.validation import PlanValidationError

if TYPE_CHECKING:
    from reflexor.orchestrator.engine.core import OrchestratorEngine


async def enqueue_tasks(
    engine: OrchestratorEngine,
    tasks: Sequence[Task],
    *,
    reason: str,
    source: str,
    trigger: PlanningTrigger | None = None,
    first_enqueue_started_s: float | None = None,
) -> None:
    now_ms = int(engine.clock.now_ms())
    for idx, task in enumerate(tasks):
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
            await engine.queue.enqueue(envelope)
            if (
                idx == 0
                and first_enqueue_started_s is not None
                and engine.metrics is not None
                and source == "reflex"
            ):
                engine.metrics.event_to_enqueue_seconds.observe(
                    time.perf_counter() - first_enqueue_started_s
                )
                first_enqueue_started_s = None

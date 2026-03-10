from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

from reflexor.domain.enums import TaskStatus
from reflexor.domain.execution_state import ExecutionState, complete_canceled, start_execution
from reflexor.domain.lifecycle import transition_task
from reflexor.domain.models import ToolCall
from reflexor.executor.service.audit import append_audit
from reflexor.executor.service.dependencies import (
    blocked_dependents_after_failure,
    dependency_ready_envelope,
    ready_dependents_after_success,
)
from reflexor.executor.service.outcomes import classify_outcome, did_attempt_tool_run
from reflexor.executor.service.state_transitions import apply_state_transition
from reflexor.executor.service.types import (
    ApprovalPersistError,
    ExecutionDisposition,
    ExecutionReport,
)
from reflexor.security.policy.enforcement import ToolExecutionOutcome
from reflexor.storage.idempotency import IdempotencyLedger, OutcomeToCache
from reflexor.storage.ports import ApprovalRepo
from reflexor.tools.sdk import Tool, ToolResult

if TYPE_CHECKING:
    from reflexor.domain.models import Task
    from reflexor.executor.service.core import ExecutorService


def _try_get_tool(service: ExecutorService, tool_name: str) -> Tool[BaseModel] | None:
    try:
        return service._tool_registry.get(tool_name)
    except KeyError:
        return None


def _is_tool_idempotent(service: ExecutorService, tool_name: str) -> bool:
    tool = _try_get_tool(service, tool_name)
    return bool(tool is not None and tool.manifest.idempotent)


async def persist_started(
    service: ExecutorService,
    *,
    task: Task,
    tool_call: ToolCall,
) -> ExecutionState:
    now_ms = int(service._clock.now_ms())
    state = start_execution(task=task, tool_call=tool_call, now_ms=now_ms)

    uow = service._uow_factory()
    async with uow:
        session = uow.session
        task_repo = service._repos.task_repo(session)
        tool_call_repo = service._repos.tool_call_repo(session)

        await tool_call_repo.update(state.tool_call)
        await task_repo.update(state.task)

    return state


async def persist_approval(
    service: ExecutorService, *, approval_id: str, approval_repo: ApprovalRepo
) -> None:
    existing = await approval_repo.get(approval_id)
    if existing is not None:
        return

    approval = await service._policy_runner.approvals.get(approval_id)
    if approval is None:
        raise ApprovalPersistError(f"approval not found in store: {approval_id!r}")

    await approval_repo.create(approval)


async def record_ledger(
    *,
    ledger: IdempotencyLedger,
    tool_call: ToolCall,
    result: ToolResult,
    disposition: ExecutionDisposition,
) -> None:
    outcome = OutcomeToCache(tool_name=tool_call.tool_name, result=result)
    if result.ok:
        await ledger.record_success(tool_call.idempotency_key, outcome)
        return

    transient = disposition == ExecutionDisposition.FAILED_TRANSIENT
    await ledger.record_failure(tool_call.idempotency_key, outcome, transient=transient)


async def persist_outcome(
    service: ExecutorService,
    task: Task,
    tool_call: ToolCall,
    outcome: ToolExecutionOutcome,
    *,
    tool_latency_s: float | None = None,
) -> ExecutionReport:
    decision = outcome.decision
    result = outcome.result

    disposition, retry_after_s = classify_outcome(
        task=task,
        outcome=outcome,
        retry_policy=service._retry_policy,
    )
    now_ms = int(service._clock.now_ms())
    state = apply_state_transition(
        task=task,
        tool_call=tool_call,
        decision=decision,
        disposition=disposition,
        now_ms=now_ms,
    )

    will_retry = disposition == ExecutionDisposition.FAILED_TRANSIENT and int(
        state.task.attempts
    ) < int(state.task.max_attempts)

    attempted_tool_run = did_attempt_tool_run(outcome)
    tool_is_idempotent = _is_tool_idempotent(service, tool_call.tool_name)
    dependent_envelopes = []

    uow = service._uow_factory()
    async with uow:
        session = uow.session
        task_repo = service._repos.task_repo(session)
        tool_call_repo = service._repos.tool_call_repo(session)
        approval_repo = service._repos.approval_repo(session)
        run_packet_repo = service._repos.run_packet_repo(session)
        ledger = service._ledger_factory(session)

        if outcome.approval_id is not None:
            await persist_approval(
                service,
                approval_id=outcome.approval_id,
                approval_repo=approval_repo,
            )

        await tool_call_repo.update(state.tool_call)
        await task_repo.update(state.task)

        await append_audit(
            run_packet_repo=run_packet_repo,
            task=state.task,
            tool_call=state.tool_call,
            decision=decision,
            result=result,
            disposition=disposition,
            retry_after_s=retry_after_s,
            will_retry=will_retry,
            approval_id=outcome.approval_id,
            approval_status=outcome.approval_status,
            guard_decision=outcome.guard_decision,
            now_ms=now_ms,
            settings=service._policy_runner.gate.settings,
        )

        if tool_is_idempotent and attempted_tool_run:
            await record_ledger(
                ledger=ledger,
                tool_call=state.tool_call,
                result=result,
                disposition=disposition,
            )

        all_run_tasks = await task_repo.list_by_run(state.task.run_id)
        if state.task.status == TaskStatus.SUCCEEDED:
            ready_dependents = ready_dependents_after_success(
                task=state.task,
                all_tasks=all_run_tasks,
            )
            for dependent in ready_dependents:
                queued_task = transition_task(dependent, TaskStatus.QUEUED)
                await task_repo.update(queued_task)
                dependent_envelopes.append(
                    dependency_ready_envelope(
                        task=queued_task,
                        now_ms=now_ms,
                        upstream_task_id=state.task.task_id,
                    )
                )
        elif state.task.status in {TaskStatus.CANCELED, TaskStatus.FAILED} and not will_retry:
            blocked_dependents = blocked_dependents_after_failure(
                task=state.task,
                all_tasks=all_run_tasks,
            )
            for dependent in blocked_dependents:
                if dependent.tool_call is None:
                    continue
                canceled_state = complete_canceled(
                    task=dependent,
                    tool_call=dependent.tool_call,
                    now_ms=now_ms,
                )
                await tool_call_repo.update(canceled_state.tool_call)
                await task_repo.update(canceled_state.task)

    if service._metrics is not None:
        service._metrics.tasks_completed_total.labels(status=state.task.status.value).inc()

        if will_retry:
            error_code = (result.error_code or "unknown").strip() or "unknown"
            service._metrics.executor_retries_total.labels(
                tool_name=tool_call.tool_name,
                error_code=error_code,
            ).inc()

        if tool_latency_s is not None and attempted_tool_run:
            service._metrics.tool_latency_seconds.labels(
                tool_name=tool_call.tool_name,
                ok="true" if result.ok else "false",
            ).observe(float(tool_latency_s))

    for envelope in dependent_envelopes:
        await service._queue.enqueue(envelope)

    return ExecutionReport(
        task_id=state.task.task_id,
        run_id=state.task.run_id,
        tool_call_id=state.tool_call.tool_call_id,
        tool_name=state.tool_call.tool_name,
        idempotency_key=state.tool_call.idempotency_key,
        disposition=disposition,
        used_cached_result=False,
        retry_after_s=retry_after_s,
        decision=decision,
        result=result,
        approval_id=outcome.approval_id,
        approval_status=outcome.approval_status,
    )

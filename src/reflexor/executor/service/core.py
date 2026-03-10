"""Executor single-task execution pipeline.

The executor is application-layer code: it consumes queued task IDs, enforces policy, executes
tools, and persists outcomes. This module provides a DI-friendly `ExecutorService` focused on
executing *one* task at a time (the worker loop lives elsewhere).

Clean Architecture:
- Allowed dependencies: `reflexor.domain`, storage ports/UoW, queue interface contracts,
  tool boundary types/registries, and the policy enforcement boundary.
- Forbidden dependencies: FastAPI/Starlette, CLI entrypoints, and infrastructure adapters.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from reflexor.domain.enums import TaskStatus, ToolCallStatus
from reflexor.executor.concurrency import ConcurrencyLimiter
from reflexor.executor.retries import RetryPolicy
from reflexor.executor.service.audit import append_audit
from reflexor.executor.service.cache import maybe_use_cached_success
from reflexor.executor.service.circuit_breaker import record_circuit_breaker_result
from reflexor.executor.service.dependencies import has_unmet_dependencies
from reflexor.executor.service.loading import load_approval_status, load_task_and_tool_call
from reflexor.executor.service.persistence import (
    persist_approval,
    persist_outcome,
    persist_started,
)
from reflexor.executor.service.types import (
    ExecutionDisposition,
    ExecutionReport,
    ExecutorRepoFactory,
)
from reflexor.guards.circuit_breaker.interface import CircuitBreaker
from reflexor.observability.context import correlation_context
from reflexor.observability.metrics import ReflexorMetrics
from reflexor.orchestrator.clock import Clock
from reflexor.orchestrator.queue import Lease, Queue
from reflexor.security.policy.enforcement import (
    EXECUTION_DELAYED_ERROR_CODE,
    PolicyEnforcedToolRunner,
)
from reflexor.storage.idempotency import IdempotencyLedger
from reflexor.storage.uow import DatabaseSession, UnitOfWork
from reflexor.tools.context import tool_context_from_settings
from reflexor.tools.registry import ToolRegistry
from reflexor.tools.sdk import ToolResult


class ExecutorService:
    """Execute a single task through idempotency, policy, tool execution, and persistence."""

    def __init__(
        self,
        *,
        uow_factory: Callable[[], UnitOfWork],
        repos: ExecutorRepoFactory,
        queue: Queue,
        policy_runner: PolicyEnforcedToolRunner,
        tool_registry: ToolRegistry,
        idempotency_ledger: Callable[[DatabaseSession], IdempotencyLedger],
        retry_policy: RetryPolicy,
        limiter: ConcurrencyLimiter,
        clock: Clock,
        metrics: ReflexorMetrics | None = None,
        circuit_breaker: CircuitBreaker | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._repos = repos
        self._queue = queue
        self._policy_runner = policy_runner
        self._tool_registry = tool_registry
        self._ledger_factory = idempotency_ledger
        self._retry_policy = retry_policy
        self._limiter = limiter
        self._clock = clock
        self._metrics = metrics
        self._circuit_breaker = circuit_breaker

    @property
    def queue(self) -> Queue:
        return self._queue

    @property
    def retry_policy(self) -> RetryPolicy:
        return self._retry_policy

    async def execute_task(self, task_id: str) -> ExecutionReport:
        """Execute a single task by ID and return an execution report.

        This method is designed to be called by a worker loop that handles queue leasing.
        """

        normalized_task_id = task_id.strip()
        if not normalized_task_id:
            raise ValueError("task_id must be non-empty")

        loaded = await load_task_and_tool_call(self, normalized_task_id)
        task = loaded.task
        tool_call = loaded.tool_call

        with correlation_context(
            run_id=task.run_id,
            task_id=task.task_id,
            tool_call_id=tool_call.tool_call_id,
        ):
            if task.status == TaskStatus.CANCELED:
                return ExecutionReport(
                    task_id=task.task_id,
                    run_id=task.run_id,
                    tool_call_id=tool_call.tool_call_id,
                    tool_name=tool_call.tool_name,
                    idempotency_key=tool_call.idempotency_key,
                    disposition=ExecutionDisposition.CANCELED,
                    decision=None,
                    result=ToolResult(
                        ok=False,
                        error_code="TASK_CANCELED",
                        error_message="task is canceled; skipping execution",
                    ),
                )

            cached = await maybe_use_cached_success(self, task, tool_call)
            if cached is not None:
                return cached

            ctx = tool_context_from_settings(
                self._policy_runner.gate.settings,
                timeout_s=float(task.timeout_s),
            )

            async def on_before_execute() -> None:
                nonlocal task, tool_call
                if task.status == TaskStatus.RUNNING and tool_call.status == ToolCallStatus.RUNNING:
                    return
                started = await persist_started(self, task=task, tool_call=tool_call)
                task = started.task
                tool_call = started.tool_call

            async with self._limiter.limit(tool_call.tool_name):
                tool_latency_s: float | None = None
                if self._metrics is None:
                    outcome = await self._policy_runner.execute_tool_call(
                        tool_call,
                        ctx=ctx,
                        on_before_execute=on_before_execute,
                    )
                else:
                    started_s = time.perf_counter()
                    outcome = await self._policy_runner.execute_tool_call(
                        tool_call,
                        ctx=ctx,
                        on_before_execute=on_before_execute,
                    )
                    tool_latency_s = time.perf_counter() - started_s

            await record_circuit_breaker_result(self, tool_call=tool_call, outcome=outcome)

            if outcome.result.error_code == EXECUTION_DELAYED_ERROR_CODE:
                delay_s = 0.0
                debug = outcome.result.debug or {}
                raw_delay_s = debug.get("delay_s")
                if isinstance(raw_delay_s, (int, float, str)):
                    try:
                        parsed_delay_s = float(raw_delay_s)
                    except ValueError:
                        parsed_delay_s = 0.0
                    delay_s = max(0.0, parsed_delay_s)

                will_retry = int(task.attempts) < int(task.max_attempts)
                uow = self._uow_factory()
                async with uow:
                    session = uow.session
                    approval_repo = self._repos.approval_repo(session)
                    run_packet_repo = self._repos.run_packet_repo(session)

                    if outcome.approval_id is not None:
                        await persist_approval(
                            self,
                            approval_id=outcome.approval_id,
                            approval_repo=approval_repo,
                        )

                    now_ms = int(self._clock.now_ms())
                    await append_audit(
                        run_packet_repo=run_packet_repo,
                        task=task,
                        tool_call=tool_call,
                        decision=outcome.decision,
                        result=outcome.result,
                        disposition=ExecutionDisposition.FAILED_TRANSIENT,
                        retry_after_s=delay_s,
                        will_retry=will_retry,
                        approval_id=outcome.approval_id,
                        approval_status=outcome.approval_status,
                        guard_decision=outcome.guard_decision,
                        now_ms=now_ms,
                        settings=self._policy_runner.gate.settings,
                    )

                return ExecutionReport(
                    task_id=task.task_id,
                    run_id=task.run_id,
                    tool_call_id=tool_call.tool_call_id,
                    tool_name=tool_call.tool_name,
                    idempotency_key=tool_call.idempotency_key,
                    disposition=ExecutionDisposition.FAILED_TRANSIENT,
                    used_cached_result=False,
                    retry_after_s=delay_s,
                    decision=outcome.decision,
                    result=outcome.result,
                    approval_id=outcome.approval_id,
                    approval_status=outcome.approval_status,
                )

            return await persist_outcome(
                self,
                task,
                tool_call,
                outcome,
                tool_latency_s=tool_latency_s,
            )

    async def process_lease(self, lease: Lease) -> ExecutionReport:
        """Execute a leased queue item and apply ack/nack retry scheduling.

        Queue semantics:
        - Terminal outcomes are acked.
        - Transient failures are nacked with a backoff delay when attempts remain.
        - Approval-required outcomes are acked after transitioning to WAITING_APPROVAL (no retries).
        """

        task_id = lease.envelope.task_id
        loaded = await load_task_and_tool_call(self, task_id)
        task = loaded.task
        tool_call = loaded.tool_call

        with correlation_context(
            run_id=task.run_id,
            task_id=task.task_id,
            tool_call_id=tool_call.tool_call_id,
        ):
            uow = self._uow_factory()
            async with uow:
                run_tasks = await self._repos.task_repo(uow.session).list_by_run(task.run_id)
            if task.status in {TaskStatus.PENDING, TaskStatus.QUEUED} and has_unmet_dependencies(
                task=task, all_tasks=run_tasks
            ):
                await self._queue.nack(
                    lease,
                    delay_s=1.0,
                    reason="dependencies_unmet",
                )
                return ExecutionReport(
                    task_id=task.task_id,
                    run_id=task.run_id,
                    tool_call_id=tool_call.tool_call_id,
                    tool_name=tool_call.tool_name,
                    idempotency_key=tool_call.idempotency_key,
                    disposition=ExecutionDisposition.FAILED_TRANSIENT,
                    decision=None,
                    result=ToolResult(
                        ok=False,
                        error_code="DEPENDENCIES_UNMET",
                        error_message="task dependencies are not yet satisfied",
                    ),
                    retry_after_s=1.0,
                )

            if task.status == TaskStatus.SUCCEEDED:
                await self._queue.ack(lease)
                return ExecutionReport(
                    task_id=task.task_id,
                    run_id=task.run_id,
                    tool_call_id=tool_call.tool_call_id,
                    tool_name=tool_call.tool_name,
                    idempotency_key=tool_call.idempotency_key,
                    disposition=ExecutionDisposition.SUCCEEDED,
                    decision=None,
                    result=ToolResult(ok=True, data={"status": "already_succeeded"}),
                )

            if task.status == TaskStatus.WAITING_APPROVAL:
                approval_id, approval_status = await load_approval_status(
                    self, tool_call.tool_call_id
                )
                await self._queue.ack(lease)
                return ExecutionReport(
                    task_id=task.task_id,
                    run_id=task.run_id,
                    tool_call_id=tool_call.tool_call_id,
                    tool_name=tool_call.tool_name,
                    idempotency_key=tool_call.idempotency_key,
                    disposition=ExecutionDisposition.WAITING_APPROVAL,
                    decision=None,
                    result=ToolResult(
                        ok=False,
                        error_code="APPROVAL_REQUIRED",
                        error_message="task is waiting for approval; skipping execution",
                        data=None if approval_id is None else {"approval_id": approval_id},
                    ),
                    approval_id=approval_id,
                    approval_status=approval_status,
                )

            if task.status == TaskStatus.CANCELED:
                await self._queue.ack(lease)
                return ExecutionReport(
                    task_id=task.task_id,
                    run_id=task.run_id,
                    tool_call_id=tool_call.tool_call_id,
                    tool_name=tool_call.tool_name,
                    idempotency_key=tool_call.idempotency_key,
                    disposition=ExecutionDisposition.CANCELED,
                    decision=None,
                    result=ToolResult(
                        ok=False,
                        error_code="TASK_CANCELED",
                        error_message="task is canceled; skipping execution",
                    ),
                )

            if task.attempts >= task.max_attempts and task.status in {
                TaskStatus.PENDING,
                TaskStatus.QUEUED,
                TaskStatus.FAILED,
            }:
                await self._queue.ack(lease)
                return ExecutionReport(
                    task_id=task.task_id,
                    run_id=task.run_id,
                    tool_call_id=tool_call.tool_call_id,
                    tool_name=tool_call.tool_name,
                    idempotency_key=tool_call.idempotency_key,
                    disposition=ExecutionDisposition.FAILED_PERMANENT,
                    decision=None,
                    result=ToolResult(
                        ok=False,
                        error_code="MAX_ATTEMPTS_EXHAUSTED",
                        error_message="task has exhausted max_attempts; skipping execution",
                        debug={"attempts": task.attempts, "max_attempts": task.max_attempts},
                    ),
                )

            report = await self.execute_task(task_id)

            if report.disposition == ExecutionDisposition.FAILED_TRANSIENT:
                refreshed = await load_task_and_tool_call(self, task_id)
                if refreshed.task.attempts < refreshed.task.max_attempts:
                    delay_s = report.retry_after_s or 0.0
                    await self._queue.nack(
                        lease,
                        delay_s=delay_s,
                        reason=f"transient_failure:{report.result.error_code or ''}",
                    )
                else:
                    await self._queue.ack(lease)
                return report

            await self._queue.ack(lease)
            return report

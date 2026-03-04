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
from dataclasses import dataclass
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from reflexor.domain.enums import ApprovalStatus, TaskStatus, ToolCallStatus
from reflexor.domain.models import Task, ToolCall
from reflexor.domain.models_event import Event
from reflexor.domain.models_run_packet import RunPacket
from reflexor.executor.concurrency import ConcurrencyLimiter
from reflexor.executor.idempotency import IdempotencyLedger, OutcomeToCache
from reflexor.executor.retries import (
    ErrorClassifier,
    RetryDisposition,
    RetryPolicy,
    exponential_backoff_s,
)
from reflexor.executor.state import (
    ExecutionState,
    complete_canceled,
    complete_denied,
    complete_failed,
    complete_succeeded,
    mark_waiting_approval,
    start_execution,
)
from reflexor.guards.circuit_breaker.interface import CircuitBreaker
from reflexor.guards.circuit_breaker.resolver import key_for_tool_call
from reflexor.guards.decision import GuardDecision
from reflexor.observability.audit_sanitize import sanitize_tool_output
from reflexor.observability.context import correlation_context, get_correlation_ids
from reflexor.observability.metrics import ReflexorMetrics
from reflexor.orchestrator.clock import Clock
from reflexor.orchestrator.queue import Lease, Queue
from reflexor.security.policy.decision import PolicyAction, PolicyDecision
from reflexor.security.policy.enforcement import (
    EXECUTION_DELAYED_ERROR_CODE,
    PolicyEnforcedToolRunner,
    ToolExecutionOutcome,
)
from reflexor.storage.ports import ApprovalRepo, RunPacketRepo, TaskRepo, ToolCallRepo
from reflexor.storage.uow import DatabaseSession, UnitOfWork
from reflexor.tools.context import tool_context_from_settings
from reflexor.tools.registry import ToolRegistry
from reflexor.tools.sdk import Tool, ToolResult

DEFAULT_MAX_EXECUTION_RESULT_SUMMARY_BYTES = 8_000


class ExecutionDisposition(StrEnum):
    """High-level execution outcome for a single task."""

    CACHED = "cached"
    SUCCEEDED = "succeeded"
    FAILED_TRANSIENT = "failed_transient"
    FAILED_PERMANENT = "failed_permanent"
    WAITING_APPROVAL = "waiting_approval"
    DENIED = "denied"
    CANCELED = "canceled"


class ExecutionReport(BaseModel):
    """Return value for executor runs (logging/metrics friendly, JSON-safe)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str
    run_id: str
    tool_call_id: str
    tool_name: str
    idempotency_key: str
    disposition: ExecutionDisposition
    used_cached_result: bool = False
    retry_after_s: float | None = None
    decision: PolicyDecision | None = None
    result: ToolResult
    approval_id: str | None = None
    approval_status: ApprovalStatus | None = None
    correlation_ids: dict[str, str | None] = Field(default_factory=get_correlation_ids)


class ExecutorError(RuntimeError):
    """Base error for unexpected executor failures."""


class TaskNotFound(ExecutorError):
    """Raised when a task_id cannot be loaded."""


class ToolCallMissing(ExecutorError):
    """Raised when a task has no tool_call attached."""


class ApprovalPersistError(ExecutorError):
    """Raised when an approval exists but cannot be persisted."""


class RunPacketPersistError(ExecutorError):
    """Raised when run-packet persistence fails."""


@dataclass(frozen=True, slots=True)
class ExecutorRepoFactory:
    """Factories for constructing repository adapters from a UnitOfWork session."""

    task_repo: Callable[[DatabaseSession], TaskRepo]
    tool_call_repo: Callable[[DatabaseSession], ToolCallRepo]
    approval_repo: Callable[[DatabaseSession], ApprovalRepo]
    run_packet_repo: Callable[[DatabaseSession], RunPacketRepo]


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

        loaded = await self._load_task_and_tool_call(normalized_task_id)
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

            cached = await self._maybe_use_cached_success(task, tool_call)
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
                started = await self._persist_started(task=task, tool_call=tool_call)
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

            await self._record_circuit_breaker_result(tool_call=tool_call, outcome=outcome)

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
                        await self._persist_approval(
                            approval_id=outcome.approval_id,
                            approval_repo=approval_repo,
                        )

                    await self._append_audit(
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

            return await self._persist_outcome(
                task,
                tool_call,
                outcome,
                tool_latency_s=tool_latency_s,
            )

    async def _record_circuit_breaker_result(
        self,
        *,
        tool_call: ToolCall,
        outcome: ToolExecutionOutcome,
    ) -> None:
        if self._circuit_breaker is None:
            return
        if outcome.result.error_code == EXECUTION_DELAYED_ERROR_CODE:
            return
        if not self._did_attempt_tool_run(outcome):
            return

        url_value = tool_call.args.get("url") if isinstance(tool_call.args, dict) else None
        key = key_for_tool_call(
            tool_name=tool_call.tool_name,
            url=url_value if isinstance(url_value, str) else None,
        )
        now_s = float(self._clock.now_ms()) / 1000.0
        try:
            await self._circuit_breaker.record_result(
                key=key,
                ok=bool(outcome.result.ok),
                now_s=now_s,
            )
        except Exception:
            # Best-effort: never fail the task because the circuit breaker store is down.
            return

    async def process_lease(self, lease: Lease) -> ExecutionReport:
        """Execute a leased queue item and apply ack/nack retry scheduling.

        Queue semantics:
        - Terminal outcomes are acked.
        - Transient failures are nacked with a backoff delay when attempts remain.
        - Approval-required outcomes are acked after transitioning to WAITING_APPROVAL (no retries).
        """

        task_id = lease.envelope.task_id
        loaded = await self._load_task_and_tool_call(task_id)
        task = loaded.task
        tool_call = loaded.tool_call

        with correlation_context(
            run_id=task.run_id,
            task_id=task.task_id,
            tool_call_id=tool_call.tool_call_id,
        ):
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
                approval_id, approval_status = await self._load_approval_status(
                    tool_call.tool_call_id
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
                refreshed = await self._load_task_and_tool_call(task_id)
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

    async def _maybe_use_cached_success(
        self, task: Task, tool_call: ToolCall
    ) -> ExecutionReport | None:
        tool = self._try_get_tool(tool_call.tool_name)
        if tool is None or not bool(tool.manifest.idempotent):
            return None

        uow = self._uow_factory()
        async with uow:
            session = uow.session
            ledger = self._ledger_factory(session)

            cached = await ledger.get_success(tool_call.idempotency_key)
            if cached is None:
                return None
            if cached.tool_name != tool_call.tool_name:
                return None

            tool_call_repo = self._repos.tool_call_repo(session)
            task_repo = self._repos.task_repo(session)
            run_packet_repo = self._repos.run_packet_repo(session)

            now_ms = int(self._clock.now_ms())
            started = start_execution(task=task, tool_call=tool_call, now_ms=now_ms)
            completed = complete_succeeded(
                task=started.task, tool_call=started.tool_call, now_ms=now_ms
            )

            await tool_call_repo.update(completed.tool_call)
            await task_repo.update(completed.task)

            decision = PolicyDecision.allow(
                reason_code="idempotency_cache",
                message="used cached successful outcome",
                rule_id="executor.idempotency_ledger",
                metadata={"idempotency_key": tool_call.idempotency_key},
            )

            await self._append_audit(
                run_packet_repo=run_packet_repo,
                task=completed.task,
                tool_call=completed.tool_call,
                decision=decision,
                result=cached.result,
                disposition=ExecutionDisposition.CACHED,
                retry_after_s=None,
                will_retry=False,
                approval_id=None,
                approval_status=None,
            )

            if self._metrics is not None:
                self._metrics.idempotency_cache_hits_total.inc()
                self._metrics.tasks_completed_total.labels(status=completed.task.status.value).inc()

            return ExecutionReport(
                task_id=task.task_id,
                run_id=task.run_id,
                tool_call_id=tool_call.tool_call_id,
                tool_name=tool_call.tool_name,
                idempotency_key=tool_call.idempotency_key,
                disposition=ExecutionDisposition.CACHED,
                used_cached_result=True,
                decision=decision,
                result=cached.result,
            )

    async def _persist_started(self, *, task: Task, tool_call: ToolCall) -> ExecutionState:
        now_ms = int(self._clock.now_ms())
        state = start_execution(task=task, tool_call=tool_call, now_ms=now_ms)

        uow = self._uow_factory()
        async with uow:
            session = uow.session
            task_repo = self._repos.task_repo(session)
            tool_call_repo = self._repos.tool_call_repo(session)

            await tool_call_repo.update(state.tool_call)
            await task_repo.update(state.task)

        return state

    def _apply_state_transition(
        self,
        *,
        task: Task,
        tool_call: ToolCall,
        decision: PolicyDecision,
        disposition: ExecutionDisposition,
        now_ms: int,
    ) -> ExecutionState:
        _ = decision

        if disposition == ExecutionDisposition.WAITING_APPROVAL:
            return mark_waiting_approval(task=task, tool_call=tool_call)

        if disposition == ExecutionDisposition.DENIED:
            if tool_call.status == ToolCallStatus.PENDING:
                return complete_denied(task=task, tool_call=tool_call, now_ms=now_ms)
            return complete_canceled(task=task, tool_call=tool_call, now_ms=now_ms)

        if disposition == ExecutionDisposition.CANCELED:
            return complete_canceled(task=task, tool_call=tool_call, now_ms=now_ms)

        if disposition == ExecutionDisposition.SUCCEEDED:
            if task.status != TaskStatus.RUNNING or tool_call.status != ToolCallStatus.RUNNING:
                started = start_execution(task=task, tool_call=tool_call, now_ms=now_ms)
                task = started.task
                tool_call = started.tool_call
            return complete_succeeded(task=task, tool_call=tool_call, now_ms=now_ms)

        if disposition in {
            ExecutionDisposition.FAILED_TRANSIENT,
            ExecutionDisposition.FAILED_PERMANENT,
        }:
            if task.status != TaskStatus.RUNNING or tool_call.status != ToolCallStatus.RUNNING:
                started = start_execution(task=task, tool_call=tool_call, now_ms=now_ms)
                task = started.task
                tool_call = started.tool_call
            return complete_failed(task=task, tool_call=tool_call, now_ms=now_ms)

        raise ValueError(f"unhandled disposition: {disposition}")

    async def _persist_outcome(
        self,
        task: Task,
        tool_call: ToolCall,
        outcome: ToolExecutionOutcome,
        *,
        tool_latency_s: float | None = None,
    ) -> ExecutionReport:
        decision = outcome.decision
        result = outcome.result

        disposition, retry_after_s = self._classify_outcome(task, outcome)
        now_ms = int(self._clock.now_ms())
        state = self._apply_state_transition(
            task=task,
            tool_call=tool_call,
            decision=decision,
            disposition=disposition,
            now_ms=now_ms,
        )

        will_retry = disposition == ExecutionDisposition.FAILED_TRANSIENT and int(
            state.task.attempts
        ) < int(state.task.max_attempts)

        uow = self._uow_factory()
        async with uow:
            session = uow.session
            task_repo = self._repos.task_repo(session)
            tool_call_repo = self._repos.tool_call_repo(session)
            approval_repo = self._repos.approval_repo(session)
            run_packet_repo = self._repos.run_packet_repo(session)
            ledger = self._ledger_factory(session)

            if outcome.approval_id is not None:
                await self._persist_approval(
                    approval_id=outcome.approval_id,
                    approval_repo=approval_repo,
                )

            await tool_call_repo.update(state.tool_call)
            await task_repo.update(state.task)

            await self._append_audit(
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
            )

            did_attempt_tool_run = self._did_attempt_tool_run(outcome)
            tool_is_idempotent = self._is_tool_idempotent(tool_call.tool_name)
            if tool_is_idempotent and did_attempt_tool_run:
                await self._record_ledger(
                    ledger=ledger,
                    tool_call=state.tool_call,
                    result=result,
                    disposition=disposition,
                )

        if self._metrics is not None:
            self._metrics.tasks_completed_total.labels(status=state.task.status.value).inc()

            if will_retry:
                error_code = (result.error_code or "unknown").strip() or "unknown"
                self._metrics.executor_retries_total.labels(
                    tool_name=tool_call.tool_name,
                    error_code=error_code,
                ).inc()

            if tool_latency_s is not None and did_attempt_tool_run:
                self._metrics.tool_latency_seconds.labels(
                    tool_name=tool_call.tool_name,
                    ok="true" if result.ok else "false",
                ).observe(float(tool_latency_s))

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

    def _try_get_tool(self, tool_name: str) -> Tool[BaseModel] | None:
        try:
            return self._tool_registry.get(tool_name)
        except KeyError:
            return None

    def _is_tool_idempotent(self, tool_name: str) -> bool:
        tool = self._try_get_tool(tool_name)
        return bool(tool is not None and tool.manifest.idempotent)

    def _did_attempt_tool_run(self, outcome: ToolExecutionOutcome) -> bool:
        if outcome.decision.action == PolicyAction.ALLOW:
            return True
        if outcome.decision.action == PolicyAction.REQUIRE_APPROVAL:
            return outcome.approval_status == ApprovalStatus.APPROVED
        return False

    def _classify_outcome(
        self, task: Task, outcome: ToolExecutionOutcome
    ) -> tuple[ExecutionDisposition, float | None]:
        if task.status == TaskStatus.CANCELED:
            return ExecutionDisposition.CANCELED, None

        if outcome.approval_status == ApprovalStatus.DENIED:
            return ExecutionDisposition.DENIED, None

        if outcome.decision.action == PolicyAction.DENY:
            return ExecutionDisposition.DENIED, None

        if outcome.result.ok:
            return ExecutionDisposition.SUCCEEDED, None

        classifier = ErrorClassifier(policy=self._retry_policy)
        disposition = classifier.classify(outcome.result)
        if disposition == RetryDisposition.APPROVAL_REQUIRED:
            return ExecutionDisposition.WAITING_APPROVAL, None

        if disposition == RetryDisposition.TRANSIENT:
            attempt = max(1, int(task.attempts))
            retry_after_s = exponential_backoff_s(
                attempt,
                base_delay_s=self._retry_policy.base_delay_s,
                max_delay_s=self._retry_policy.max_delay_s,
            )
            return ExecutionDisposition.FAILED_TRANSIENT, retry_after_s

        return ExecutionDisposition.FAILED_PERMANENT, None

    async def _record_ledger(
        self,
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

    async def _persist_approval(self, *, approval_id: str, approval_repo: ApprovalRepo) -> None:
        existing = await approval_repo.get(approval_id)
        if existing is not None:
            return

        approval = await self._policy_runner.approvals.get(approval_id)
        if approval is None:
            raise ApprovalPersistError(f"approval not found in store: {approval_id!r}")

        await approval_repo.create(approval)

    async def _load_approval_status(
        self, tool_call_id: str
    ) -> tuple[str | None, ApprovalStatus | None]:
        uow = self._uow_factory()
        async with uow:
            session = uow.session
            approval_repo = self._repos.approval_repo(session)
            approval = await approval_repo.get_by_tool_call(tool_call_id)
            if approval is None:
                return None, None
            return approval.approval_id, approval.status

    async def _append_audit(
        self,
        *,
        run_packet_repo: RunPacketRepo,
        task: Task,
        tool_call: ToolCall,
        decision: PolicyDecision,
        result: ToolResult,
        disposition: ExecutionDisposition,
        retry_after_s: float | None,
        will_retry: bool,
        approval_id: str | None,
        approval_status: ApprovalStatus | None,
        guard_decision: GuardDecision | None = None,
    ) -> None:
        now_ms = int(self._clock.now_ms())
        packet = await run_packet_repo.get(task.run_id)
        if packet is None:
            packet = self._new_fallback_packet(task=task, now_ms=now_ms)

        settings = self._policy_runner.gate.settings
        summary_budget = min(
            int(settings.max_tool_output_bytes),
            DEFAULT_MAX_EXECUTION_RESULT_SUMMARY_BYTES,
        )
        summary_settings = settings.model_copy(update={"max_tool_output_bytes": summary_budget})

        tool_result_entry: dict[str, object] = {
            "task_id": task.task_id,
            "tool_call_id": tool_call.tool_call_id,
            "tool_name": tool_call.tool_name,
            "status": disposition.value,
            "error_code": result.error_code,
            "retry": {
                "will_retry": bool(will_retry),
                "retry_after_s": retry_after_s,
                "attempt": int(task.attempts),
                "max_attempts": int(task.max_attempts),
            },
            "policy_decision": {
                "action": decision.action.value,
                "reason_code": decision.reason_code,
                "rule_id": decision.rule_id,
            },
            "result_summary": sanitize_tool_output(
                result.model_dump(mode="json"), settings=summary_settings
            ),
            "approval_id": approval_id,
            "approval_status": None if approval_status is None else approval_status.value,
            "recorded_at_ms": now_ms,
        }
        if guard_decision is not None:
            tool_result_entry["guard_decision"] = guard_decision.model_dump(mode="json")
        decision_entry: dict[str, object] = {
            "task_id": task.task_id,
            "tool_call_id": tool_call.tool_call_id,
            **decision.to_audit_dict(),
        }

        updated = packet.with_tool_result_added(tool_result_entry).with_policy_decision_added(
            decision_entry
        )

        try:
            await run_packet_repo.create(updated)
        except Exception as exc:  # pragma: no cover
            raise RunPacketPersistError("failed to persist run packet") from exc

    def _new_fallback_packet(self, *, task: Task, now_ms: int) -> RunPacket:
        return RunPacket(
            run_id=task.run_id,
            event=Event(
                event_id=str(uuid4()),
                type="executor.task",
                source="executor",
                received_at_ms=now_ms,
                payload={"task_id": task.task_id, "run_id": task.run_id},
            ),
            tasks=[task],
            created_at_ms=now_ms,
        )

    async def _load_task_and_tool_call(self, task_id: str) -> _LoadedTask:
        uow = self._uow_factory()
        async with uow:
            session = uow.session
            task_repo = self._repos.task_repo(session)
            task = await task_repo.get(task_id)
            if task is None:
                raise TaskNotFound(f"unknown task_id: {task_id!r}")

            tool_call = task.tool_call
            if tool_call is None:
                raise ToolCallMissing(f"task has no tool_call: {task.task_id!r}")

            return _LoadedTask(task=task, tool_call=tool_call)


class _LoadedTask(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task: Task
    tool_call: ToolCall


__all__ = [
    "ApprovalPersistError",
    "ExecutionDisposition",
    "ExecutionReport",
    "ExecutorRepoFactory",
    "ExecutorService",
    "RunPacketPersistError",
    "TaskNotFound",
    "ToolCallMissing",
]

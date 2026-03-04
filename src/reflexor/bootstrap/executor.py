"""Bootstrap wiring for executor service components."""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession

from reflexor.bootstrap.repos import RepoProviders
from reflexor.config import ReflexorSettings
from reflexor.executor.concurrency import ConcurrencyLimiter
from reflexor.executor.retries import RetryPolicy
from reflexor.executor.service import ExecutorRepoFactory, ExecutorService
from reflexor.guards.circuit_breaker.interface import CircuitBreaker
from reflexor.infra.db.repos import SqlAlchemyIdempotencyLedger
from reflexor.observability.metrics import ReflexorMetrics
from reflexor.orchestrator.clock import Clock
from reflexor.orchestrator.queue import Queue
from reflexor.security.policy.enforcement import PolicyEnforcedToolRunner
from reflexor.storage.idempotency import IdempotencyLedger
from reflexor.storage.uow import DatabaseSession, UnitOfWork
from reflexor.tools.registry import ToolRegistry


def build_executor_service(
    *,
    settings: ReflexorSettings,
    metrics: ReflexorMetrics | None,
    uow_factory: Callable[[], UnitOfWork],
    repos: RepoProviders,
    queue: Queue,
    policy_runner: PolicyEnforcedToolRunner,
    tool_registry: ToolRegistry,
    clock: Clock,
    circuit_breaker: CircuitBreaker | None,
    concurrency: int | None = None,
) -> tuple[ExecutorService, int]:
    effective_concurrency = int(settings.executor_max_concurrency)
    if concurrency is not None:
        effective_concurrency = int(concurrency)
    if effective_concurrency <= 0:
        raise ValueError("concurrency must be > 0")

    per_tool = {
        name: min(int(limit), effective_concurrency)
        for name, limit in settings.executor_per_tool_concurrency.items()
    }
    limiter = ConcurrencyLimiter(max_global=effective_concurrency, per_tool=per_tool)

    retry_policy = RetryPolicy(
        base_delay_s=float(settings.executor_retry_base_delay_s),
        max_delay_s=float(settings.executor_retry_max_delay_s),
        jitter=float(settings.executor_retry_jitter),
    )

    executor_repos = ExecutorRepoFactory(
        task_repo=repos.task_repo,
        tool_call_repo=repos.tool_call_repo,
        approval_repo=repos.approval_repo,
        run_packet_repo=repos.run_packet_repo,
    )

    def ledger_factory(session: DatabaseSession) -> IdempotencyLedger:
        return SqlAlchemyIdempotencyLedger(cast(AsyncSession, session), settings=settings)

    executor = ExecutorService(
        uow_factory=uow_factory,
        repos=executor_repos,
        queue=queue,
        policy_runner=policy_runner,
        tool_registry=tool_registry,
        idempotency_ledger=ledger_factory,
        retry_policy=retry_policy,
        limiter=limiter,
        clock=clock,
        metrics=metrics,
        circuit_breaker=circuit_breaker,
    )

    return executor, effective_concurrency

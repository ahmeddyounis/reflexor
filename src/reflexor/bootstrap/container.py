"""Application composition root container.

This module wires concrete adapters (infra) to application services and exposes a single
`AppContainer` object that outer layers (API/CLI/worker) can depend on.

Clean Architecture:
- This is an outer-layer composition root. It may import infrastructure adapters.
- Route/command modules should depend on services, not directly on SQLAlchemy sessions.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from reflexor.application.approvals_service import ApprovalCommandService
from reflexor.application.services import (
    ApprovalsService,
    EventSubmissionService,
    QueryService,
    RunQueryService,
    TaskQueryService,
)
from reflexor.application.suppressions_service import (
    EventSuppressionCommandService,
    EventSuppressionQueryService,
)
from reflexor.bootstrap.orchestrator import build_orchestrator_engine
from reflexor.bootstrap.policy import build_policy_gate, build_policy_runner
from reflexor.bootstrap.queue import build_queue
from reflexor.bootstrap.repos import RepoProviders, build_repo_providers
from reflexor.bootstrap.tools import build_tool_runner
from reflexor.bootstrap.uow import build_uow_factory
from reflexor.config import ReflexorSettings, get_settings
from reflexor.domain.enums import ApprovalStatus
from reflexor.executor.concurrency import ConcurrencyLimiter
from reflexor.executor.idempotency import IdempotencyLedger
from reflexor.executor.retries import RetryPolicy
from reflexor.executor.service import ExecutorRepoFactory, ExecutorService
from reflexor.guards.circuit_breaker.interface import CircuitBreaker
from reflexor.infra.db.engine import (
    AsyncSessionFactory,
    create_async_engine,
    create_async_session_factory,
)
from reflexor.infra.db.repos import SqlAlchemyIdempotencyLedger
from reflexor.observability.metrics import ReflexorMetrics
from reflexor.orchestrator.clock import Clock
from reflexor.orchestrator.engine import OrchestratorEngine
from reflexor.orchestrator.interfaces import Planner, ReflexRouter
from reflexor.orchestrator.queue import Queue
from reflexor.orchestrator.sinks import RunPacketSink
from reflexor.security.policy.enforcement import PolicyEnforcedToolRunner
from reflexor.security.policy.gate import PolicyGate
from reflexor.storage.uow import DatabaseSession, UnitOfWork
from reflexor.tools.builtin_registry import build_builtin_registry
from reflexor.tools.registry import ToolRegistry
from reflexor.tools.runner import ToolRunner

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AppContainer:
    """Application container stored on `app.state.container`."""

    settings: ReflexorSettings
    metrics: ReflexorMetrics
    engine: AsyncEngine
    session_factory: AsyncSessionFactory
    uow_factory: Callable[[], UnitOfWork]
    repos: RepoProviders
    queue: Queue
    tool_registry: ToolRegistry
    tool_runner: ToolRunner
    policy_gate: PolicyGate
    policy_runner: PolicyEnforcedToolRunner
    circuit_breaker: CircuitBreaker
    orchestrator_engine: OrchestratorEngine

    submit_events: EventSubmissionService
    approvals: ApprovalsService
    approval_commands: ApprovalCommandService
    queries: QueryService
    run_queries: RunQueryService
    task_queries: TaskQueryService
    suppression_queries: EventSuppressionQueryService
    suppression_commands: EventSuppressionCommandService

    _owns_engine: bool = True
    _owns_queue: bool = True

    async def ensure_queue_ready(
        self,
        *,
        timeout_s: float | None = 1.0,
        required: bool = False,
        log: logging.Logger | None = None,
    ) -> bool:
        """Best-effort queue initialization for first-run deployments."""

        effective_logger = logger if log is None else log

        ensure_ready = getattr(self.queue, "ensure_ready", None)
        if ensure_ready is None:
            return True

        try:
            result = ensure_ready()
            if inspect.isawaitable(result):
                if timeout_s is None:
                    await result
                else:
                    await asyncio.wait_for(result, timeout=float(timeout_s))
        except Exception:
            effective_logger.exception(
                "queue ensure_ready failed",
                extra={
                    "event_type": "queue.ensure_ready.failed",
                    "queue_backend": self.settings.queue_backend,
                    "required": required,
                },
            )
            if required:
                raise
            return False

        return True

    async def start(self) -> None:
        """Start any background application tasks."""

        await self.ensure_queue_ready(timeout_s=1.0, required=False)

        self.orchestrator_engine.start()

    async def ping_db(self, *, timeout_s: float = 1.0) -> bool:
        async def _ping() -> None:
            async with self.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))

        try:
            await asyncio.wait_for(_ping(), timeout=timeout_s)
        except Exception:
            return False
        return True

    async def ping_queue(self, *, timeout_s: float = 0.2) -> bool:
        if self.settings.queue_backend == "inmemory":
            return True

        from reflexor.infra.queue.redis_streams import RedisStreamsQueue

        if not isinstance(self.queue, RedisStreamsQueue):
            return False

        try:
            return bool(await self.queue.ping(timeout_s=float(timeout_s)))
        except Exception:
            return False

    async def count_pending_approvals(self, *, timeout_s: float = 1.0) -> int | None:
        async def _count() -> int:
            uow = self.uow_factory()
            async with uow:
                repo = self.repos.approval_repo(uow.session)
                return await repo.count(status=ApprovalStatus.PENDING)

        try:
            return int(await asyncio.wait_for(_count(), timeout=timeout_s))
        except Exception:
            return None

    async def aclose(self) -> None:
        """Close resources owned by this container."""

        try:
            await self.orchestrator_engine.aclose()
        finally:
            aclose = getattr(self.circuit_breaker, "aclose", None)
            if aclose is not None:
                result = aclose()
                if inspect.isawaitable(result):
                    await result
            if self._owns_queue:
                await self.queue.aclose()
            if self._owns_engine:
                await self.engine.dispose()

    def build_executor_service(
        self,
        *,
        concurrency: int | None = None,
    ) -> tuple[ExecutorService, int]:
        """Build an `ExecutorService` wired to this container's ports/adapters.

        This is intended for outer-layer composition roots (e.g. CLI `run worker`) so they can
        reuse the same wiring as the API container while keeping executor construction in one place.
        """

        effective_concurrency = int(self.settings.executor_max_concurrency)
        if concurrency is not None:
            effective_concurrency = int(concurrency)
        if effective_concurrency <= 0:
            raise ValueError("concurrency must be > 0")

        per_tool = {
            name: min(int(limit), effective_concurrency)
            for name, limit in self.settings.executor_per_tool_concurrency.items()
        }
        limiter = ConcurrencyLimiter(max_global=effective_concurrency, per_tool=per_tool)

        retry_policy = RetryPolicy(
            base_delay_s=float(self.settings.executor_retry_base_delay_s),
            max_delay_s=float(self.settings.executor_retry_max_delay_s),
            jitter=float(self.settings.executor_retry_jitter),
        )

        repos = ExecutorRepoFactory(
            task_repo=self.repos.task_repo,
            tool_call_repo=self.repos.tool_call_repo,
            approval_repo=self.repos.approval_repo,
            run_packet_repo=self.repos.run_packet_repo,
        )

        def ledger_factory(session: DatabaseSession) -> IdempotencyLedger:
            return SqlAlchemyIdempotencyLedger(
                cast(AsyncSession, session),
                settings=self.settings,
            )

        executor = ExecutorService(
            uow_factory=self.uow_factory,
            repos=repos,
            queue=self.queue,
            policy_runner=self.policy_runner,
            tool_registry=self.tool_registry,
            idempotency_ledger=ledger_factory,
            retry_policy=retry_policy,
            limiter=limiter,
            clock=self.orchestrator_engine.clock,
            metrics=self.metrics,
            circuit_breaker=self.circuit_breaker,
        )
        return executor, effective_concurrency

    @classmethod
    def build(
        cls,
        *,
        settings: ReflexorSettings | None = None,
        metrics: ReflexorMetrics | None = None,
        engine: AsyncEngine | None = None,
        session_factory: AsyncSessionFactory | None = None,
        queue: Queue | None = None,
        tool_registry: ToolRegistry | None = None,
        reflex_router: ReflexRouter | None = None,
        planner: Planner | None = None,
        clock: Clock | None = None,
        run_sink: RunPacketSink | None = None,
    ) -> AppContainer:
        effective_settings = get_settings() if settings is None else settings
        effective_metrics = ReflexorMetrics.build() if metrics is None else metrics

        owns_engine = engine is None
        effective_engine = engine or create_async_engine(effective_settings)
        effective_session_factory = session_factory or create_async_session_factory(
            effective_engine
        )

        uow_factory = build_uow_factory(effective_session_factory)
        repos = build_repo_providers(effective_settings)
        effective_queue, owns_queue = build_queue(
            effective_settings,
            metrics=effective_metrics,
            queue=queue,
        )

        registry = tool_registry or build_builtin_registry(settings=effective_settings)
        tool_runner = build_tool_runner(effective_settings, registry=registry)
        policy_gate = build_policy_gate(effective_settings, metrics=effective_metrics)
        policy_runner, circuit_breaker = build_policy_runner(
            metrics=effective_metrics,
            uow_factory=uow_factory,
            repos=repos,
            registry=registry,
            runner=tool_runner,
            gate=policy_gate,
        )
        orchestrator_engine = build_orchestrator_engine(
            effective_settings,
            metrics=effective_metrics,
            uow_factory=uow_factory,
            repos=repos,
            queue=effective_queue,
            registry=registry,
            reflex_router=reflex_router,
            planner=planner,
            clock=clock,
            run_sink=run_sink,
        )

        submit_events = EventSubmissionService(
            orchestrator=orchestrator_engine,
            uow_factory=uow_factory,
            event_repo=repos.event_repo,
            run_packet_repo=repos.run_packet_repo,
        )
        approvals = ApprovalsService(uow_factory=uow_factory, approval_repo=repos.approval_repo)
        approval_commands = ApprovalCommandService(
            uow_factory=uow_factory,
            approval_repo=repos.approval_repo,
            task_repo=repos.task_repo,
            tool_call_repo=repos.tool_call_repo,
            queue=effective_queue,
            clock=orchestrator_engine.clock,
        )
        queries = QueryService(
            uow_factory=uow_factory,
            task_repo=repos.task_repo,
            run_packet_repo=repos.run_packet_repo,
        )
        run_queries = RunQueryService(
            uow_factory=uow_factory,
            run_repo=repos.run_repo,
            run_packet_repo=repos.run_packet_repo,
        )
        task_queries = TaskQueryService(uow_factory=uow_factory, task_repo=repos.task_repo)
        suppression_queries = EventSuppressionQueryService(
            uow_factory=uow_factory,
            repo=repos.event_suppression_repo,
            clock=orchestrator_engine.clock,
        )
        suppression_commands = EventSuppressionCommandService(
            uow_factory=uow_factory,
            repo=repos.event_suppression_repo,
            clock=orchestrator_engine.clock,
        )

        return cls(
            settings=effective_settings,
            metrics=effective_metrics,
            engine=effective_engine,
            session_factory=effective_session_factory,
            uow_factory=uow_factory,
            repos=repos,
            queue=effective_queue,
            tool_registry=registry,
            tool_runner=tool_runner,
            policy_gate=policy_gate,
            policy_runner=policy_runner,
            circuit_breaker=circuit_breaker,
            orchestrator_engine=orchestrator_engine,
            submit_events=submit_events,
            approvals=approvals,
            approval_commands=approval_commands,
            queries=queries,
            run_queries=run_queries,
            task_queries=task_queries,
            suppression_queries=suppression_queries,
            suppression_commands=suppression_commands,
            _owns_engine=owns_engine,
            _owns_queue=owns_queue,
        )


__all__ = ["AppContainer", "RepoProviders"]

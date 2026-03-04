"""API composition root container.

This module wires concrete adapters (infra) to application services and exposes a single
`AppContainer` object that API routes can depend on.

Clean Architecture:
- This is an outer-layer composition root. It may import infrastructure adapters.
- Route modules should depend on services, not directly on SQLAlchemy sessions.
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

from reflexor.api.metrics import ApiMetrics
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
from reflexor.config import ReflexorSettings, get_settings
from reflexor.domain.enums import ApprovalStatus
from reflexor.executor.approval_store import DbApprovalStore
from reflexor.executor.concurrency import ConcurrencyLimiter
from reflexor.executor.idempotency import IdempotencyLedger
from reflexor.executor.retries import RetryPolicy
from reflexor.executor.service import ExecutorRepoFactory, ExecutorService
from reflexor.guards.circuit_breaker.interface import CircuitBreaker
from reflexor.guards.defaults import (
    build_default_circuit_breaker,
    build_default_policy_guard_chain,
)
from reflexor.infra.db.engine import (
    AsyncSessionFactory,
    create_async_engine,
    create_async_session_factory,
)
from reflexor.infra.db.repos import (
    SqlAlchemyApprovalRepo,
    SqlAlchemyEventRepo,
    SqlAlchemyEventSuppressionRepo,
    SqlAlchemyIdempotencyLedger,
    SqlAlchemyRunPacketRepo,
    SqlAlchemyRunRepo,
    SqlAlchemyTaskRepo,
    SqlAlchemyToolCallRepo,
)
from reflexor.infra.db.unit_of_work import SqlAlchemyUnitOfWork
from reflexor.infra.queue.factory import build_queue
from reflexor.observability.queue_observers import (
    CompositeQueueObserver,
    LoggingQueueObserver,
    PrometheusQueueObserver,
)
from reflexor.orchestrator.budgets import BudgetLimits
from reflexor.orchestrator.clock import Clock, SystemClock
from reflexor.orchestrator.engine import OrchestratorEngine
from reflexor.orchestrator.event_suppression import DbEventSuppressor
from reflexor.orchestrator.interfaces import (
    NeedsPlanningRouter,
    NoOpPlanner,
    Planner,
    ReflexRouter,
)
from reflexor.orchestrator.persistence import OrchestratorPersistence, OrchestratorRepoFactory
from reflexor.orchestrator.queue import Queue
from reflexor.orchestrator.sinks import NoopRunPacketSink, RunPacketSink
from reflexor.security.policy.defaults import build_default_policy_rules
from reflexor.security.policy.enforcement import PolicyEnforcedToolRunner
from reflexor.security.policy.gate import PolicyGate
from reflexor.storage.ports import (
    ApprovalRepo,
    EventRepo,
    EventSuppressionRepo,
    RunPacketRepo,
    RunRepo,
    TaskRepo,
    ToolCallRepo,
)
from reflexor.storage.uow import DatabaseSession, UnitOfWork
from reflexor.tools.builtin_registry import build_builtin_registry
from reflexor.tools.registry import ToolRegistry
from reflexor.tools.runner import ToolRunner
from reflexor.tools.sandbox_policy import SandboxPolicy, SandboxPolicyBackend

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RepoProviders:
    event_repo: Callable[[DatabaseSession], EventRepo]
    event_suppression_repo: Callable[[DatabaseSession], EventSuppressionRepo]
    run_repo: Callable[[DatabaseSession], RunRepo]
    task_repo: Callable[[DatabaseSession], TaskRepo]
    tool_call_repo: Callable[[DatabaseSession], ToolCallRepo]
    approval_repo: Callable[[DatabaseSession], ApprovalRepo]
    run_packet_repo: Callable[[DatabaseSession], RunPacketRepo]


def _build_uow_factory(
    session_factory: AsyncSessionFactory,
) -> Callable[[], UnitOfWork]:
    def uow_factory() -> UnitOfWork:
        return SqlAlchemyUnitOfWork(session_factory)

    return uow_factory


def _build_repo_providers(settings: ReflexorSettings) -> RepoProviders:
    return RepoProviders(
        event_repo=lambda session: SqlAlchemyEventRepo(cast(AsyncSession, session)),
        event_suppression_repo=lambda session: SqlAlchemyEventSuppressionRepo(
            cast(AsyncSession, session)
        ),
        run_repo=lambda session: SqlAlchemyRunRepo(cast(AsyncSession, session)),
        task_repo=lambda session: SqlAlchemyTaskRepo(cast(AsyncSession, session)),
        tool_call_repo=lambda session: SqlAlchemyToolCallRepo(cast(AsyncSession, session)),
        approval_repo=lambda session: SqlAlchemyApprovalRepo(cast(AsyncSession, session)),
        run_packet_repo=lambda session: SqlAlchemyRunPacketRepo(
            cast(AsyncSession, session), settings=settings
        ),
    )


def _build_queue(
    settings: ReflexorSettings,
    *,
    metrics: ApiMetrics,
    queue: Queue | None,
) -> tuple[Queue, bool]:
    owns_queue = queue is None
    if queue is not None:
        return queue, owns_queue

    queue_observer = CompositeQueueObserver(
        observers=[
            PrometheusQueueObserver(metrics=metrics),
            LoggingQueueObserver(),
        ]
    )
    return build_queue(settings, observer=queue_observer), owns_queue


def _build_tool_runner(
    settings: ReflexorSettings,
    *,
    registry: ToolRegistry,
) -> ToolRunner:
    sandbox_policy = SandboxPolicy.from_settings(settings)
    sandbox_backend = SandboxPolicyBackend(policy=sandbox_policy)
    return ToolRunner(
        registry=registry,
        settings=settings,
        backend=sandbox_backend,
    )


def _build_policy_gate(
    settings: ReflexorSettings,
    *,
    metrics: ApiMetrics,
) -> PolicyGate:
    return PolicyGate(
        rules=build_default_policy_rules(),
        settings=settings,
        metrics=metrics,
    )


def _build_policy_runner(
    *,
    metrics: ApiMetrics,
    uow_factory: Callable[[], UnitOfWork],
    repos: RepoProviders,
    registry: ToolRegistry,
    runner: ToolRunner,
    gate: PolicyGate,
) -> tuple[PolicyEnforcedToolRunner, CircuitBreaker]:
    approval_store = DbApprovalStore(uow_factory=uow_factory, approval_repo=repos.approval_repo)

    circuit_breaker = build_default_circuit_breaker()
    guard_chain = build_default_policy_guard_chain(
        gate=gate,
        metrics=metrics,
        circuit_breaker=circuit_breaker,
    )

    policy_runner = PolicyEnforcedToolRunner(
        registry=registry,
        runner=runner,
        gate=gate,
        approvals=approval_store,
        metrics=metrics,
        guard_chain=guard_chain,
    )

    return policy_runner, circuit_breaker


def _resolve_reflex_router(
    settings: ReflexorSettings,
    reflex_router: ReflexRouter | None,
) -> ReflexRouter:
    if reflex_router is not None:
        return reflex_router

    rules_path = getattr(settings, "reflex_rules_path", None)
    if rules_path is not None:
        from reflexor.orchestrator.reflex_rules import RuleBasedReflexRouter

        try:
            return RuleBasedReflexRouter.from_json_file(rules_path)
        except FileNotFoundError as exc:
            raise ValueError(f"reflex_rules_path not found: {rules_path}") from exc
        except Exception as exc:  # pragma: no cover
            raise ValueError(f"failed to load reflex rules from {rules_path}: {exc}") from exc

    return NeedsPlanningRouter()


def _build_orchestrator_engine(
    settings: ReflexorSettings,
    *,
    metrics: ApiMetrics,
    uow_factory: Callable[[], UnitOfWork],
    repos: RepoProviders,
    queue: Queue,
    registry: ToolRegistry,
    reflex_router: ReflexRouter | None,
    planner: Planner | None,
    clock: Clock | None,
    run_sink: RunPacketSink | None,
) -> OrchestratorEngine:
    limits = BudgetLimits(
        max_tasks_per_run=settings.max_tasks_per_run,
        max_tool_calls_per_run=settings.max_tool_calls_per_run,
        max_wall_time_s=settings.max_run_wall_time_s,
        max_events_per_planning_cycle=settings.max_events_per_planning_cycle,
        max_backlog_events=settings.event_backlog_max,
    )

    effective_clock = clock or SystemClock()

    orchestrator_repos = OrchestratorRepoFactory(
        event_repo=repos.event_repo,
        run_repo=repos.run_repo,
        task_repo=repos.task_repo,
        tool_call_repo=repos.tool_call_repo,
        run_packet_repo=repos.run_packet_repo,
    )
    persistence = OrchestratorPersistence(uow_factory=uow_factory, repos=orchestrator_repos)

    event_suppressor = None
    if settings.event_suppression_enabled:
        event_suppressor = DbEventSuppressor(
            uow_factory=uow_factory,
            repo=repos.event_suppression_repo,
            clock=effective_clock,
            signature_fields=tuple(settings.event_suppression_signature_fields),
            window_s=float(settings.event_suppression_window_s),
            threshold=int(settings.event_suppression_threshold),
            ttl_s=float(settings.event_suppression_ttl_s),
        )

    effective_reflex_router = _resolve_reflex_router(settings, reflex_router)

    return OrchestratorEngine(
        reflex_router=effective_reflex_router,
        planner=NoOpPlanner() if planner is None else planner,
        tool_registry=registry,
        queue=queue,
        run_sink=NoopRunPacketSink() if run_sink is None else run_sink,
        persistence=persistence,
        event_suppressor=event_suppressor,
        limits=limits,
        clock=effective_clock,
        metrics=metrics,
        planner_debounce_s=float(settings.planner_debounce_s),
        planner_interval_s=float(settings.planner_interval_s),
    )


@dataclass(slots=True)
class AppContainer:
    """Application container stored on `app.state.container`."""

    settings: ReflexorSettings
    metrics: ApiMetrics
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

    async def start(self) -> None:
        """Start any background application tasks."""

        ensure_ready = getattr(self.queue, "ensure_ready", None)
        if ensure_ready is not None:
            try:
                result = ensure_ready()
                if inspect.isawaitable(result):
                    await asyncio.wait_for(result, timeout=1.0)
            except Exception:
                logger.exception(
                    "queue ensure_ready failed",
                    extra={
                        "event_type": "queue.ensure_ready.failed",
                        "queue_backend": self.settings.queue_backend,
                    },
                )

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
        metrics: ApiMetrics | None = None,
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
        effective_metrics = ApiMetrics.build() if metrics is None else metrics

        owns_engine = engine is None
        effective_engine = engine or create_async_engine(effective_settings)
        effective_session_factory = session_factory or create_async_session_factory(
            effective_engine
        )

        uow_factory = _build_uow_factory(effective_session_factory)
        repos = _build_repo_providers(effective_settings)
        effective_queue, owns_queue = _build_queue(
            effective_settings,
            metrics=effective_metrics,
            queue=queue,
        )

        registry = tool_registry or build_builtin_registry(settings=effective_settings)
        tool_runner = _build_tool_runner(effective_settings, registry=registry)
        policy_gate = _build_policy_gate(effective_settings, metrics=effective_metrics)
        policy_runner, circuit_breaker = _build_policy_runner(
            metrics=effective_metrics,
            uow_factory=uow_factory,
            repos=repos,
            registry=registry,
            runner=tool_runner,
            gate=policy_gate,
        )
        orchestrator_engine = _build_orchestrator_engine(
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

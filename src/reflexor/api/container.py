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
from reflexor.guards import GuardChain, PolicyGuard
from reflexor.guards.circuit_breaker import (
    CircuitBreakerGuard,
    CircuitBreakerSpec,
    InMemoryCircuitBreaker,
)
from reflexor.guards.circuit_breaker.interface import CircuitBreaker
from reflexor.guards.rate_limit import InMemoryRateLimiter
from reflexor.guards.rate_limit.guard import RateLimitGuard
from reflexor.guards.rate_limit.policy import RateLimitPolicy
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

        def uow_factory() -> UnitOfWork:
            return SqlAlchemyUnitOfWork(effective_session_factory)

        repos = RepoProviders(
            event_repo=lambda session: SqlAlchemyEventRepo(cast(AsyncSession, session)),
            event_suppression_repo=lambda session: SqlAlchemyEventSuppressionRepo(
                cast(AsyncSession, session)
            ),
            run_repo=lambda session: SqlAlchemyRunRepo(cast(AsyncSession, session)),
            task_repo=lambda session: SqlAlchemyTaskRepo(cast(AsyncSession, session)),
            tool_call_repo=lambda session: SqlAlchemyToolCallRepo(cast(AsyncSession, session)),
            approval_repo=lambda session: SqlAlchemyApprovalRepo(cast(AsyncSession, session)),
            run_packet_repo=lambda session: SqlAlchemyRunPacketRepo(
                cast(AsyncSession, session), settings=effective_settings
            ),
        )

        owns_queue = queue is None
        queue_observer = CompositeQueueObserver(
            observers=[
                PrometheusQueueObserver(metrics=effective_metrics),
                LoggingQueueObserver(),
            ]
        )
        effective_queue = queue or build_queue(effective_settings, observer=queue_observer)

        registry = tool_registry or build_builtin_registry(settings=effective_settings)
        sandbox_policy = SandboxPolicy.from_settings(effective_settings)
        sandbox_backend = SandboxPolicyBackend(policy=sandbox_policy)
        tool_runner = ToolRunner(
            registry=registry,
            settings=effective_settings,
            backend=sandbox_backend,
        )

        policy_gate = PolicyGate(
            rules=build_default_policy_rules(),
            settings=effective_settings,
            metrics=effective_metrics,
        )

        approval_store = DbApprovalStore(uow_factory=uow_factory, approval_repo=repos.approval_repo)

        circuit_breaker_spec = CircuitBreakerSpec(
            failure_threshold=5,
            window_s=60.0,
            open_cooldown_s=10.0,
            half_open_max_calls=1,
            success_threshold=1,
        )
        circuit_breaker = InMemoryCircuitBreaker(spec=circuit_breaker_spec)
        rate_limiter = InMemoryRateLimiter()
        rate_limit_policy = RateLimitPolicy(settings=effective_settings, limiter=rate_limiter)
        guard_chain = GuardChain(
            [
                PolicyGuard(gate=policy_gate),
                CircuitBreakerGuard(breaker=circuit_breaker, metrics=effective_metrics),
                RateLimitGuard(policy=rate_limit_policy),
            ]
        )
        policy_runner = PolicyEnforcedToolRunner(
            registry=registry,
            runner=tool_runner,
            gate=policy_gate,
            approvals=approval_store,
            metrics=effective_metrics,
            guard_chain=guard_chain,
        )

        orchestrator_repos = OrchestratorRepoFactory(
            event_repo=repos.event_repo,
            run_repo=repos.run_repo,
            task_repo=repos.task_repo,
            tool_call_repo=repos.tool_call_repo,
            run_packet_repo=repos.run_packet_repo,
        )
        persistence = OrchestratorPersistence(uow_factory=uow_factory, repos=orchestrator_repos)

        limits = BudgetLimits(
            max_tasks_per_run=effective_settings.max_tasks_per_run,
            max_tool_calls_per_run=effective_settings.max_tool_calls_per_run,
            max_wall_time_s=effective_settings.max_run_wall_time_s,
            max_events_per_planning_cycle=effective_settings.max_events_per_planning_cycle,
            max_backlog_events=effective_settings.event_backlog_max,
        )

        effective_clock = clock or SystemClock()
        effective_reflex_router = reflex_router
        if effective_reflex_router is None:
            rules_path = getattr(effective_settings, "reflex_rules_path", None)
            if rules_path is not None:
                from reflexor.orchestrator.reflex_rules import RuleBasedReflexRouter

                try:
                    effective_reflex_router = RuleBasedReflexRouter.from_json_file(rules_path)
                except FileNotFoundError as exc:
                    raise ValueError(f"reflex_rules_path not found: {rules_path}") from exc
                except Exception as exc:  # pragma: no cover
                    raise ValueError(
                        f"failed to load reflex rules from {rules_path}: {exc}"
                    ) from exc
            else:
                effective_reflex_router = NeedsPlanningRouter()

        event_suppressor = None
        if effective_settings.event_suppression_enabled:
            event_suppressor = DbEventSuppressor(
                uow_factory=uow_factory,
                repo=repos.event_suppression_repo,
                clock=effective_clock,
                signature_fields=tuple(effective_settings.event_suppression_signature_fields),
                window_s=float(effective_settings.event_suppression_window_s),
                threshold=int(effective_settings.event_suppression_threshold),
                ttl_s=float(effective_settings.event_suppression_ttl_s),
            )
        orchestrator_engine = OrchestratorEngine(
            reflex_router=effective_reflex_router,
            planner=NoOpPlanner() if planner is None else planner,
            tool_registry=registry,
            queue=effective_queue,
            run_sink=NoopRunPacketSink() if run_sink is None else run_sink,
            persistence=persistence,
            event_suppressor=event_suppressor,
            limits=limits,
            clock=effective_clock,
            metrics=effective_metrics,
            planner_debounce_s=float(effective_settings.planner_debounce_s),
            planner_interval_s=float(effective_settings.planner_interval_s),
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
            clock=effective_clock,
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
            clock=effective_clock,
        )
        suppression_commands = EventSuppressionCommandService(
            uow_factory=uow_factory,
            repo=repos.event_suppression_repo,
            clock=effective_clock,
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

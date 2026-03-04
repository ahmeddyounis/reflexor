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
from reflexor.config import ReflexorSettings, get_settings
from reflexor.domain.enums import ApprovalStatus
from reflexor.executor.approval_store import DbApprovalStore
from reflexor.infra.db.engine import (
    AsyncSessionFactory,
    create_async_engine,
    create_async_session_factory,
)
from reflexor.infra.db.repos import (
    SqlAlchemyApprovalRepo,
    SqlAlchemyEventRepo,
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
from reflexor.orchestrator.interfaces import (
    NeedsPlanningRouter,
    NoOpPlanner,
    Planner,
    ReflexRouter,
)
from reflexor.orchestrator.persistence import OrchestratorPersistence, OrchestratorRepoFactory
from reflexor.orchestrator.queue import Queue
from reflexor.orchestrator.sinks import NoopRunPacketSink, RunPacketSink
from reflexor.security.policy.enforcement import PolicyEnforcedToolRunner
from reflexor.security.policy.gate import PolicyGate
from reflexor.security.policy.rules import (
    ApprovalRequiredRule,
    NetworkAllowlistRule,
    ScopeEnabledRule,
    WorkspaceRule,
)
from reflexor.storage.ports import (
    ApprovalRepo,
    EventRepo,
    RunPacketRepo,
    RunRepo,
    TaskRepo,
    ToolCallRepo,
)
from reflexor.storage.uow import DatabaseSession, UnitOfWork
from reflexor.tools.fs_tool import FsListDirTool, FsReadTextTool, FsWriteTextTool
from reflexor.tools.http_tool import HttpTool
from reflexor.tools.registry import ToolRegistry
from reflexor.tools.runner import ToolRunner
from reflexor.tools.webhook_tool import WebhookEmitTool


@dataclass(frozen=True, slots=True)
class RepoProviders:
    event_repo: Callable[[DatabaseSession], EventRepo]
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
    orchestrator_engine: OrchestratorEngine

    submit_events: EventSubmissionService
    approvals: ApprovalsService
    approval_commands: ApprovalCommandService
    queries: QueryService
    run_queries: RunQueryService
    task_queries: TaskQueryService

    _owns_engine: bool = True
    _owns_queue: bool = True

    async def start(self) -> None:
        """Start any background application tasks."""

        ensure_ready = getattr(self.queue, "ensure_ready", None)
        if ensure_ready is not None:
            result = ensure_ready()
            if inspect.isawaitable(result):
                await result

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
            if self._owns_queue:
                await self.queue.aclose()
            if self._owns_engine:
                await self.engine.dispose()

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

        registry = tool_registry or _build_default_tool_registry(effective_settings)
        tool_runner = ToolRunner(registry=registry, settings=effective_settings)

        policy_gate = PolicyGate(
            rules=[
                ScopeEnabledRule(),
                NetworkAllowlistRule(),
                WorkspaceRule(),
                ApprovalRequiredRule(),
            ],
            settings=effective_settings,
            metrics=effective_metrics,
        )

        approval_store = DbApprovalStore(uow_factory=uow_factory, approval_repo=repos.approval_repo)

        policy_runner = PolicyEnforcedToolRunner(
            registry=registry,
            runner=tool_runner,
            gate=policy_gate,
            approvals=approval_store,
            metrics=effective_metrics,
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
        orchestrator_engine = OrchestratorEngine(
            reflex_router=effective_reflex_router,
            planner=NoOpPlanner() if planner is None else planner,
            tool_registry=registry,
            queue=effective_queue,
            run_sink=NoopRunPacketSink() if run_sink is None else run_sink,
            persistence=persistence,
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
            orchestrator_engine=orchestrator_engine,
            submit_events=submit_events,
            approvals=approvals,
            approval_commands=approval_commands,
            queries=queries,
            run_queries=run_queries,
            task_queries=task_queries,
            _owns_engine=owns_engine,
            _owns_queue=owns_queue,
        )


def _build_default_tool_registry(settings: ReflexorSettings) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(FsReadTextTool(settings=settings))
    registry.register(FsWriteTextTool(settings=settings))
    registry.register(FsListDirTool(settings=settings))
    registry.register(HttpTool(settings=settings))
    registry.register(WebhookEmitTool(settings=settings))
    return registry


__all__ = ["AppContainer", "RepoProviders"]

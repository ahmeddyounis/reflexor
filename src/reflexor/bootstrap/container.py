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
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from reflexor.bootstrap.executor import build_executor_service
from reflexor.bootstrap.orchestrator import build_orchestrator_engine
from reflexor.bootstrap.planner import build_planner
from reflexor.bootstrap.policy import build_policy_gate, build_policy_runner
from reflexor.bootstrap.queue import build_queue
from reflexor.bootstrap.repos import RepoProviders, build_repo_providers
from reflexor.bootstrap.services import AppServices, build_app_services
from reflexor.bootstrap.tools import build_tool_runner
from reflexor.bootstrap.uow import build_uow_factory
from reflexor.config import ReflexorSettings, get_settings
from reflexor.domain.enums import ApprovalStatus
from reflexor.executor.service import ExecutorService
from reflexor.guards.circuit_breaker.interface import CircuitBreaker
from reflexor.infra.db.engine import (
    AsyncSessionFactory,
    create_async_engine,
    create_async_session_factory,
)
from reflexor.observability.metrics import ReflexorMetrics
from reflexor.observability.tracing import configure_tracing
from reflexor.orchestrator.clock import Clock
from reflexor.orchestrator.engine import OrchestratorEngine
from reflexor.orchestrator.interfaces import Planner, ReflexClassifier, ReflexRouter
from reflexor.orchestrator.plans import PlanningInput
from reflexor.orchestrator.queue import Queue
from reflexor.orchestrator.sinks import RunPacketSink
from reflexor.security.policy.enforcement import PolicyEnforcedToolRunner
from reflexor.security.policy.gate import PolicyGate
from reflexor.storage.uow import UnitOfWork
from reflexor.tools.builtin_registry import build_builtin_registry
from reflexor.tools.registry import ToolRegistry
from reflexor.tools.runner import ToolRunner

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
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


def _engine_from_session_factory(session_factory: AsyncSessionFactory) -> AsyncEngine | None:
    """Best-effort extraction of an engine from an async_sessionmaker."""

    kw = getattr(session_factory, "kw", None)
    if not isinstance(kw, dict):
        return None

    bind = kw.get("bind")
    if isinstance(bind, AsyncEngine):
        return bind

    return None


def _resolve_engine_and_session_factory(
    *,
    settings: ReflexorSettings,
    engine: AsyncEngine | None,
    session_factory: AsyncSessionFactory | None,
) -> tuple[AsyncEngine, AsyncSessionFactory, bool]:
    if engine is not None:
        if session_factory is not None:
            bound_engine = _engine_from_session_factory(session_factory)
            if bound_engine is not None and bound_engine is not engine:
                raise ValueError("engine and session_factory must use the same AsyncEngine")

        effective_session_factory = session_factory or create_async_session_factory(engine)
        return engine, effective_session_factory, False

    if session_factory is not None:
        bound_engine = _engine_from_session_factory(session_factory)
        if bound_engine is None:
            raise ValueError(
                "session_factory provided without engine, but its bound AsyncEngine could not "
                "be determined"
            )
        return bound_engine, session_factory, False

    effective_engine = create_async_engine(settings)
    effective_session_factory = create_async_session_factory(effective_engine)
    return effective_engine, effective_session_factory, True


def _memory_loader(
    *,
    settings: ReflexorSettings,
    uow_factory: Callable[[], UnitOfWork],
    repos: RepoProviders,
) -> Callable[[object], Awaitable[list[dict[str, object]]]] | None:
    if int(settings.planner_max_memory_items) <= 0:
        return None

    async def load_memory(_planning_input: PlanningInput) -> list[dict[str, object]]:
        uow = uow_factory()
        async with uow:
            repo = repos.memory_repo(uow.session)
            remaining = int(settings.planner_max_memory_items)
            items = []
            seen_memory_ids: set[str] = set()

            for event in _planning_input.events:
                if remaining <= 0:
                    break
                matching = await repo.list_recent(
                    limit=remaining,
                    offset=0,
                    event_type=event.type,
                    event_source=event.source,
                )
                for item in matching:
                    if item.memory_id in seen_memory_ids:
                        continue
                    seen_memory_ids.add(item.memory_id)
                    items.append(item)
                    remaining -= 1
                    if remaining <= 0:
                        break

            if remaining > 0:
                fallback = await repo.list_recent(limit=remaining, offset=0)
                for item in fallback:
                    if item.memory_id in seen_memory_ids:
                        continue
                    seen_memory_ids.add(item.memory_id)
                    items.append(item)
                    remaining -= 1
                    if remaining <= 0:
                        break

            return [item.to_planning_dict() for item in items]

    return load_memory


@dataclass(frozen=True, slots=True)
class _AppResources:
    engine: AsyncEngine
    session_factory: AsyncSessionFactory
    uow_factory: Callable[[], UnitOfWork]
    queue: Queue
    owns_engine: bool
    owns_queue: bool


@dataclass(frozen=True, slots=True)
class _AppPolicy:
    tool_registry: ToolRegistry
    tool_runner: ToolRunner
    policy_gate: PolicyGate
    policy_runner: PolicyEnforcedToolRunner
    circuit_breaker: CircuitBreaker


@dataclass(slots=True)
class AppContainer:
    """Application container stored on `app.state.container`."""

    settings: ReflexorSettings
    metrics: ReflexorMetrics
    resources: _AppResources
    repos: RepoProviders
    policy: _AppPolicy
    orchestrator_engine: OrchestratorEngine
    services: AppServices

    @property
    def engine(self) -> AsyncEngine:
        return self.resources.engine

    @property
    def session_factory(self) -> AsyncSessionFactory:
        return self.resources.session_factory

    @property
    def uow_factory(self) -> Callable[[], UnitOfWork]:
        return self.resources.uow_factory

    @property
    def queue(self) -> Queue:
        return self.resources.queue

    @property
    def tool_registry(self) -> ToolRegistry:
        return self.policy.tool_registry

    @property
    def tool_runner(self) -> ToolRunner:
        return self.policy.tool_runner

    @property
    def policy_gate(self) -> PolicyGate:
        return self.policy.policy_gate

    @property
    def policy_runner(self) -> PolicyEnforcedToolRunner:
        return self.policy.policy_runner

    @property
    def circuit_breaker(self) -> CircuitBreaker:
        return self.policy.circuit_breaker

    @property
    def submit_events(self) -> EventSubmissionService:
        return self.services.submit_events

    @property
    def approvals(self) -> ApprovalsService:
        return self.services.approvals

    @property
    def approval_commands(self) -> ApprovalCommandService:
        return self.services.approval_commands

    @property
    def queries(self) -> QueryService:
        return self.services.queries

    @property
    def run_queries(self) -> RunQueryService:
        return self.services.run_queries

    @property
    def task_queries(self) -> TaskQueryService:
        return self.services.task_queries

    @property
    def suppression_queries(self) -> EventSuppressionQueryService:
        return self.services.suppression_queries

    @property
    def suppression_commands(self) -> EventSuppressionCommandService:
        return self.services.suppression_commands

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
            if self.resources.owns_queue:
                await self.queue.aclose()
            if self.resources.owns_engine:
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

        return build_executor_service(
            settings=self.settings,
            metrics=self.metrics,
            uow_factory=self.uow_factory,
            repos=self.repos,
            queue=self.queue,
            policy_runner=self.policy_runner,
            tool_registry=self.tool_registry,
            clock=self.orchestrator_engine.clock,
            circuit_breaker=self.circuit_breaker,
            concurrency=concurrency,
        )

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
        reflex_classifier: ReflexClassifier | None = None,
        planner: Planner | None = None,
        clock: Clock | None = None,
        run_sink: RunPacketSink | None = None,
    ) -> AppContainer:
        effective_settings = get_settings() if settings is None else settings
        effective_metrics = ReflexorMetrics.build() if metrics is None else metrics
        configure_tracing(effective_settings)

        effective_engine, effective_session_factory, owns_engine = (
            _resolve_engine_and_session_factory(
                settings=effective_settings,
                engine=engine,
                session_factory=session_factory,
            )
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
        effective_planner = planner or build_planner(
            effective_settings,
            registry=registry,
            memory_loader=_memory_loader(
                settings=effective_settings,
                uow_factory=uow_factory,
                repos=repos,
            ),
        )
        orchestrator_engine = build_orchestrator_engine(
            effective_settings,
            metrics=effective_metrics,
            uow_factory=uow_factory,
            repos=repos,
            queue=effective_queue,
            registry=registry,
            reflex_router=reflex_router,
            reflex_classifier=reflex_classifier,
            planner=effective_planner,
            clock=clock,
            run_sink=run_sink,
        )

        services = build_app_services(
            orchestrator_engine=orchestrator_engine,
            uow_factory=uow_factory,
            repos=repos,
            queue=effective_queue,
        )

        resources = _AppResources(
            engine=effective_engine,
            session_factory=effective_session_factory,
            uow_factory=uow_factory,
            queue=effective_queue,
            owns_engine=owns_engine,
            owns_queue=owns_queue,
        )
        policy = _AppPolicy(
            tool_registry=registry,
            tool_runner=tool_runner,
            policy_gate=policy_gate,
            policy_runner=policy_runner,
            circuit_breaker=circuit_breaker,
        )

        return cls(
            settings=effective_settings,
            metrics=effective_metrics,
            resources=resources,
            repos=repos,
            policy=policy,
            orchestrator_engine=orchestrator_engine,
            services=services,
        )


__all__ = ["AppContainer", "RepoProviders"]

"""Bootstrap wiring for orchestrator components.

This module lives in the outer-layer composition root (`reflexor.bootstrap`) and is
allowed to import infrastructure adapters.
"""

from __future__ import annotations

from collections.abc import Callable

from reflexor.bootstrap.repos import RepoProviders
from reflexor.config import ReflexorSettings
from reflexor.observability.metrics import ReflexorMetrics
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
from reflexor.storage.uow import UnitOfWork
from reflexor.tools.registry import ToolRegistry


def resolve_reflex_router(
    settings: ReflexorSettings,
    reflex_router: ReflexRouter | None,
) -> ReflexRouter:
    if reflex_router is not None:
        return reflex_router

    rules_path = getattr(settings, "reflex_rules_path", None)
    if rules_path is not None:
        from reflexor.orchestrator.reflex_rules import RuleBasedReflexRouter

        try:
            return RuleBasedReflexRouter.from_file(rules_path)
        except FileNotFoundError as exc:
            raise ValueError(f"reflex_rules_path not found: {rules_path}") from exc
        except Exception as exc:  # pragma: no cover
            raise ValueError(f"failed to load reflex rules from {rules_path}: {exc}") from exc

    return NeedsPlanningRouter()


def build_orchestrator_engine(
    settings: ReflexorSettings,
    *,
    metrics: ReflexorMetrics,
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

    effective_reflex_router = resolve_reflex_router(settings, reflex_router)

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
        approval_required_scopes=tuple(settings.approval_required_scopes),
    )

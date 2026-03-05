"""Bootstrap wiring for application-layer services."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

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
from reflexor.bootstrap.repos import RepoProviders
from reflexor.orchestrator.engine import OrchestratorEngine
from reflexor.orchestrator.queue import Queue
from reflexor.storage.uow import UnitOfWork


@dataclass(frozen=True, slots=True)
class AppServices:
    submit_events: EventSubmissionService
    approvals: ApprovalsService
    approval_commands: ApprovalCommandService
    queries: QueryService
    run_queries: RunQueryService
    task_queries: TaskQueryService
    suppression_queries: EventSuppressionQueryService
    suppression_commands: EventSuppressionCommandService


def build_app_services(
    *,
    orchestrator_engine: OrchestratorEngine,
    uow_factory: Callable[[], UnitOfWork],
    repos: RepoProviders,
    queue: Queue,
) -> AppServices:
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
        queue=queue,
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

    return AppServices(
        submit_events=submit_events,
        approvals=approvals,
        approval_commands=approval_commands,
        queries=queries,
        run_queries=run_queries,
        task_queries=task_queries,
        suppression_queries=suppression_queries,
        suppression_commands=suppression_commands,
    )

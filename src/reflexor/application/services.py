"""Application services used by outer interfaces (API/CLI)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from reflexor.domain.enums import ApprovalStatus, RunStatus, TaskStatus
from reflexor.domain.models import Approval, Task
from reflexor.domain.models_event import Event
from reflexor.domain.models_run_packet import RunPacket
from reflexor.orchestrator.engine import OrchestratorEngine
from reflexor.storage.ports import (
    ApprovalRepo,
    RunPacketRepo,
    RunRepo,
    RunSummary,
    TaskRepo,
    TaskSummary,
)
from reflexor.storage.uow import DatabaseSession, UnitOfWork


@dataclass(frozen=True, slots=True)
class SubmitEventOutcome:
    event_id: str
    run_id: str | None
    duplicate: bool


@dataclass(frozen=True, slots=True)
class EventSubmissionService:
    """Submit events into the orchestrator."""

    orchestrator: OrchestratorEngine

    async def submit_event(self, event: Event) -> SubmitEventOutcome:
        outcome = await self.orchestrator.submit_event(event)
        return SubmitEventOutcome(
            event_id=outcome.event_id,
            run_id=outcome.run_id,
            duplicate=outcome.duplicate,
        )


@dataclass(frozen=True, slots=True)
class ApprovalsService:
    """Read/update approval state (HITL gating)."""

    uow_factory: Callable[[], UnitOfWork]
    approval_repo: Callable[[DatabaseSession], ApprovalRepo]

    async def list_pending(self, *, limit: int, offset: int) -> list[Approval]:
        uow = self.uow_factory()
        async with uow:
            repo = self.approval_repo(uow.session)
            return await repo.list(limit=limit, offset=offset, status=ApprovalStatus.PENDING)

    async def get(self, approval_id: str) -> Approval | None:
        uow = self.uow_factory()
        async with uow:
            repo = self.approval_repo(uow.session)
            return await repo.get(approval_id)

    async def decide(
        self,
        approval_id: str,
        *,
        decision: ApprovalStatus,
        decided_by: str | None,
    ) -> Approval:
        uow = self.uow_factory()
        async with uow:
            repo = self.approval_repo(uow.session)
            return await repo.update_status(approval_id, decision, decided_by=decided_by)


@dataclass(frozen=True, slots=True)
class QueryService:
    """Read-path queries for runs/tasks used by outer interfaces."""

    uow_factory: Callable[[], UnitOfWork]
    task_repo: Callable[[DatabaseSession], TaskRepo]
    run_packet_repo: Callable[[DatabaseSession], RunPacketRepo]

    async def get_task(self, task_id: str) -> Task | None:
        uow = self.uow_factory()
        async with uow:
            repo = self.task_repo(uow.session)
            return await repo.get(task_id)

    async def list_tasks(
        self,
        *,
        limit: int,
        offset: int,
        run_id: str | None = None,
    ) -> list[Task]:
        uow = self.uow_factory()
        async with uow:
            repo = self.task_repo(uow.session)
            return await repo.list(limit=limit, offset=offset, run_id=run_id)

    async def get_run_packet(self, run_id: str) -> RunPacket | None:
        uow = self.uow_factory()
        async with uow:
            repo = self.run_packet_repo(uow.session)
            return await repo.get(run_id)

    async def list_recent_run_packets(self, *, limit: int, offset: int) -> list[RunPacket]:
        uow = self.uow_factory()
        async with uow:
            repo = self.run_packet_repo(uow.session)
            return await repo.list_recent(limit=limit, offset=offset)


@dataclass(frozen=True, slots=True)
class RunQueryService:
    """Read-path run queries for admin/API interfaces."""

    uow_factory: Callable[[], UnitOfWork]
    run_repo: Callable[[DatabaseSession], RunRepo]
    run_packet_repo: Callable[[DatabaseSession], RunPacketRepo]

    async def list_runs(
        self,
        *,
        limit: int,
        offset: int,
        status: RunStatus | None = None,
        since_ms: int | None = None,
    ) -> tuple[list[RunSummary], int]:
        uow = self.uow_factory()
        async with uow:
            repo = self.run_repo(uow.session)
            total = await repo.count_summaries(status=status, created_after_ms=since_ms)
            items = await repo.list_summaries(
                limit=limit,
                offset=offset,
                status=status,
                created_after_ms=since_ms,
            )
            return items, total

    async def get_run_summary(self, run_id: str) -> RunSummary | None:
        uow = self.uow_factory()
        async with uow:
            repo = self.run_repo(uow.session)
            return await repo.get_summary(run_id)

    async def get_run_packet(self, run_id: str) -> RunPacket | None:
        uow = self.uow_factory()
        async with uow:
            repo = self.run_packet_repo(uow.session)
            return await repo.get(run_id)


@dataclass(frozen=True, slots=True)
class TaskQueryService:
    """Read-path task queries for admin/API interfaces."""

    uow_factory: Callable[[], UnitOfWork]
    task_repo: Callable[[DatabaseSession], TaskRepo]

    async def list_tasks(
        self,
        *,
        limit: int,
        offset: int,
        run_id: str | None = None,
        status: TaskStatus | None = None,
    ) -> tuple[list[TaskSummary], int]:
        uow = self.uow_factory()
        async with uow:
            repo = self.task_repo(uow.session)
            total = await repo.count_summaries(run_id=run_id, status=status)
            items = await repo.list_summaries(
                limit=limit,
                offset=offset,
                run_id=run_id,
                status=status,
            )
            return items, total


__all__ = [
    "ApprovalsService",
    "EventSubmissionService",
    "QueryService",
    "RunQueryService",
    "TaskQueryService",
    "SubmitEventOutcome",
]

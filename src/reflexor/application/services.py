"""Application services used by outer interfaces (API/CLI).

These services implement narrow use-cases and depend on storage ports / application-layer
components, not on infrastructure adapters.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from reflexor.domain.enums import ApprovalStatus, RunStatus
from reflexor.domain.models import Approval, Task
from reflexor.domain.models_event import Event
from reflexor.domain.models_run_packet import RunPacket
from reflexor.orchestrator.engine import OrchestratorEngine
from reflexor.storage.ports import (
    ApprovalRepo,
    EventRepo,
    RunPacketRepo,
    RunRepo,
    RunSummary,
    TaskRepo,
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
    uow_factory: Callable[[], UnitOfWork]
    event_repo: Callable[[DatabaseSession], EventRepo]
    run_packet_repo: Callable[[DatabaseSession], RunPacketRepo]

    async def submit_event(self, event: Event) -> SubmitEventOutcome:
        if event.dedupe_key is not None:
            uow = self.uow_factory()
            async with uow:
                repo = self.event_repo(uow.session)
                existing = await repo.get_by_dedupe(
                    source=event.source, dedupe_key=event.dedupe_key
                )
                if existing is not None:
                    packets = self.run_packet_repo(uow.session)
                    run_id = await packets.get_run_id_for_event(existing.event_id)
                    return SubmitEventOutcome(
                        event_id=existing.event_id,
                        run_id=run_id,
                        duplicate=True,
                    )

        run_id = await self.orchestrator.handle_event(event)
        event_id = event.event_id

        if event.dedupe_key is not None:
            uow = self.uow_factory()
            async with uow:
                repo = self.event_repo(uow.session)
                stored = await repo.get_by_dedupe(source=event.source, dedupe_key=event.dedupe_key)
                if stored is not None:
                    event_id = stored.event_id

        return SubmitEventOutcome(event_id=event_id, run_id=run_id, duplicate=False)


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


__all__ = [
    "ApprovalsService",
    "EventSubmissionService",
    "QueryService",
    "RunQueryService",
    "SubmitEventOutcome",
]

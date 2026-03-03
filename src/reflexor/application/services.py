"""Application services used by outer interfaces (API/CLI).

These services implement narrow use-cases and depend on storage ports / application-layer
components, not on infrastructure adapters.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from reflexor.domain.enums import ApprovalStatus
from reflexor.domain.models import Approval, Task
from reflexor.domain.models_event import Event
from reflexor.domain.models_run_packet import RunPacket
from reflexor.orchestrator.engine import OrchestratorEngine
from reflexor.storage.ports import ApprovalRepo, RunPacketRepo, TaskRepo
from reflexor.storage.uow import DatabaseSession, UnitOfWork


@dataclass(frozen=True, slots=True)
class EventSubmissionService:
    """Submit events into the orchestrator."""

    orchestrator: OrchestratorEngine

    async def submit(self, event: Event) -> str:
        return await self.orchestrator.handle_event(event)


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


__all__ = [
    "ApprovalsService",
    "EventSubmissionService",
    "QueryService",
]

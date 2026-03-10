"""Orchestrator persistence facade.

This module provides a thin application service that persists orchestrator outputs using
storage ports (UnitOfWork + repository interfaces) while keeping the orchestrator engine
free of database concerns.

Clean Architecture:
- Orchestrator is application-layer code.
- This module may depend on `reflexor.domain` and `reflexor.storage` ports.
- Forbidden: SQLAlchemy/FastAPI/httpx/worker/API/CLI imports.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from reflexor.domain.enums import TaskStatus
from reflexor.domain.models import Task, ToolCall
from reflexor.domain.models_event import Event
from reflexor.domain.models_run_packet import RunPacket
from reflexor.storage.ports import (
    EventRepo,
    RunPacketRepo,
    RunRecord,
    RunRepo,
    TaskRepo,
    ToolCallRepo,
)
from reflexor.storage.uow import DatabaseSession, UnitOfWork


@dataclass(frozen=True, slots=True)
class OrchestratorRepoFactory:
    """Factory for constructing repository adapters from a UnitOfWork session."""

    event_repo: Callable[[DatabaseSession], EventRepo]
    run_repo: Callable[[DatabaseSession], RunRepo]
    task_repo: Callable[[DatabaseSession], TaskRepo]
    tool_call_repo: Callable[[DatabaseSession], ToolCallRepo]
    run_packet_repo: Callable[[DatabaseSession], RunPacketRepo]


@dataclass(frozen=True, slots=True)
class OrchestratorPersistence:
    """Persist orchestrator artifacts using staged UnitOfWork transactions.

    Orchestrator queueing cannot be part of a DB transaction, so persistence is performed in
    stages to preserve auditability while keeping the engine DB-agnostic:
    1) Persist Event + Run record
    2) Persist ToolCalls + Tasks (validated, but not yet enqueued)
    3) Mark enqueued tasks as queued + persist RunPacket blob
    """

    uow_factory: Callable[[], UnitOfWork]
    repos: OrchestratorRepoFactory
    queued_status: TaskStatus = TaskStatus.QUEUED
    event_dedupe_window_ms: int | None = None

    async def persist_event_and_run(self, *, event: Event, run_record: RunRecord) -> Event:
        """Persist the event and run metadata record (commit stage 1).

        Returns the stored event (which may be an existing row when dedupe is enabled).
        """
        uow = self.uow_factory()
        async with uow:
            session = uow.session

            event_repo = self.repos.event_repo(session)
            run_repo = self.repos.run_repo(session)
            stored_event: Event
            if event.dedupe_key is not None:
                stored_event, _ = await event_repo.create_or_get_by_dedupe(
                    source=event.source,
                    dedupe_key=event.dedupe_key,
                    event=event,
                    dedupe_window_ms=self.event_dedupe_window_ms,
                )
            else:
                stored_event = await event_repo.create(event)

            await run_repo.create(run_record)

        return stored_event

    async def persist_tasks_and_tool_calls(self, tasks: Sequence[Task]) -> None:
        """Persist tool calls and tasks (commit stage 2)."""

        if not tasks:
            return

        uow = self.uow_factory()
        async with uow:
            session = uow.session

            tool_call_repo = self.repos.tool_call_repo(session)
            task_repo = self.repos.task_repo(session)

            for tool_call in _collect_tool_calls(tasks):
                await tool_call_repo.create(tool_call)

            for task in tasks:
                await task_repo.create(task)

    async def finalize_run(
        self, packet: RunPacket, *, enqueued_task_ids: Sequence[str] = ()
    ) -> None:
        """Persist the run packet and mark enqueued tasks as queued (commit stage 3)."""

        uow = self.uow_factory()
        async with uow:
            session = uow.session

            task_repo = self.repos.task_repo(session)
            run_packet_repo = self.repos.run_packet_repo(session)

            for task_id in enqueued_task_ids:
                await task_repo.update_status(task_id, self.queued_status)

            await run_packet_repo.create(packet)


def _collect_tool_calls(tasks: Sequence[Task]) -> list[ToolCall]:
    tool_calls_by_id: dict[str, ToolCall] = {}
    for task in tasks:
        tool_call = task.tool_call
        if tool_call is None:
            continue
        tool_calls_by_id.setdefault(tool_call.tool_call_id, tool_call)
    return list(tool_calls_by_id.values())


__all__ = ["OrchestratorPersistence", "OrchestratorRepoFactory"]

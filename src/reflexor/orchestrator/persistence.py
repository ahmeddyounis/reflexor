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
    """Persist orchestrator artifacts in a single transaction."""

    uow_factory: Callable[[], UnitOfWork]
    repos: OrchestratorRepoFactory
    queued_status: TaskStatus = TaskStatus.PENDING

    async def persist_run(
        self, packet: RunPacket, *, enqueued_task_ids: Sequence[str] = ()
    ) -> None:
        """Persist an orchestrator run packet and related entities.

        The intent is to keep all writes within a single UnitOfWork transaction:
        - event row (idempotent when `dedupe_key` is present)
        - run record row
        - tool_calls + tasks
        - task status updates for enqueued tasks
        - run packet blob (sanitized by the repo implementation)
        """

        run_record = RunRecord(
            run_id=packet.run_id,
            parent_run_id=packet.parent_run_id,
            created_at_ms=packet.created_at_ms,
            started_at_ms=packet.started_at_ms,
            completed_at_ms=packet.completed_at_ms,
        )

        uow = self.uow_factory()
        async with uow:
            session = uow.session

            event_repo = self.repos.event_repo(session)
            run_repo = self.repos.run_repo(session)
            tool_call_repo = self.repos.tool_call_repo(session)
            task_repo = self.repos.task_repo(session)
            run_packet_repo = self.repos.run_packet_repo(session)

            event = packet.event
            if event.dedupe_key is not None:
                await event_repo.create_or_get_by_dedupe(
                    source=event.source, dedupe_key=event.dedupe_key, event=event
                )
            else:
                await event_repo.create(event)

            await run_repo.create(run_record)

            tool_calls = _collect_tool_calls(packet.tasks)
            for tool_call in tool_calls:
                await tool_call_repo.create(tool_call)

            for task in packet.tasks:
                await task_repo.create(task)

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

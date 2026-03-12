from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import create_async_engine as sa_create_async_engine
from sqlalchemy.pool import StaticPool

from reflexor.application.maintenance_service import MaintenanceService
from reflexor.config import ReflexorSettings
from reflexor.domain.enums import TaskStatus, ToolCallStatus
from reflexor.domain.models import Task, ToolCall
from reflexor.domain.models_event import Event
from reflexor.domain.models_run_packet import RunPacket
from reflexor.infra.db.engine import AsyncSessionFactory, create_async_session_factory
from reflexor.infra.db.models import Base
from reflexor.infra.db.repos import (
    SqlAlchemyEventRepo,
    SqlAlchemyMemoryRepo,
    SqlAlchemyRunPacketRepo,
    SqlAlchemyRunRepo,
    SqlAlchemyTaskRepo,
    SqlAlchemyToolCallRepo,
)
from reflexor.infra.db.unit_of_work import SqlAlchemyUnitOfWork
from reflexor.memory.models import MemoryItem
from reflexor.memory.summary import MEMORY_SUMMARY_VERSION
from reflexor.orchestrator.clock import Clock
from reflexor.storage.ports import RunRecord

_MS_PER_DAY = 86_400_000


@dataclass(slots=True)
class _FixedClock(Clock):
    now: int

    def now_ms(self) -> int:
        return self.now

    def monotonic_ms(self) -> int:
        return self.now

    async def sleep(self, seconds: float) -> None:  # pragma: no cover
        _ = seconds
        raise AssertionError("sleep should not be called in this test")


@asynccontextmanager
async def _in_memory_session_factory() -> AsyncIterator[AsyncSessionFactory]:
    engine = sa_create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = create_async_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield session_factory
    finally:
        await engine.dispose()


def _uuid() -> str:
    return str(uuid4())


@pytest.mark.asyncio
async def test_maintenance_service_compacts_prunes_archives_and_expires_dedupe(
    tmp_path,
) -> None:
    now_ms = 10 * _MS_PER_DAY
    settings = ReflexorSettings(
        workspace_root=tmp_path,
        max_run_packet_bytes=50_000,
        maintenance_batch_size=10,
        memory_compaction_after_days=1,
        memory_retention_days=7,
        archive_terminal_tasks_after_days=7,
    )

    async with _in_memory_session_factory() as session_factory:

        def uow_factory() -> SqlAlchemyUnitOfWork:
            return SqlAlchemyUnitOfWork(session_factory)

        run_id = _uuid()
        dedupe_key = "ticket:T-1"
        event = Event(
            event_id=_uuid(),
            type="ticket.created",
            source="tests",
            received_at_ms=1_000,
            payload={"ticket_id": "T-1"},
            dedupe_key=dedupe_key,
        )
        tool_call = ToolCall(
            tool_call_id=_uuid(),
            tool_name="mock.echo",
            args={"message": "done"},
            permission_scope="debug.echo",
            idempotency_key="done",
            status=ToolCallStatus.SUCCEEDED,
            created_at_ms=1_000,
            started_at_ms=1_000,
            completed_at_ms=1_100,
        )
        task = Task(
            task_id=_uuid(),
            run_id=run_id,
            name="done",
            status=TaskStatus.SUCCEEDED,
            tool_call=tool_call,
            attempts=1,
            created_at_ms=1_000,
            started_at_ms=1_000,
            completed_at_ms=1_100,
        )
        packet = RunPacket(
            run_id=run_id,
            event=event,
            tasks=[task],
            created_at_ms=1_000,
        )

        uow = uow_factory()
        async with uow:
            session = cast(AsyncSession, uow.session)
            run_repo = SqlAlchemyRunRepo(session)
            event_repo = SqlAlchemyEventRepo(session)
            tool_call_repo = SqlAlchemyToolCallRepo(session)
            task_repo = SqlAlchemyTaskRepo(session)
            memory_repo = SqlAlchemyMemoryRepo(session)
            run_packet_repo = SqlAlchemyRunPacketRepo(
                session,
                settings=settings,
                memory_repo=memory_repo,
            )

            await run_repo.create(
                RunRecord(
                    run_id=run_id,
                    parent_run_id=None,
                    created_at_ms=1_000,
                    started_at_ms=None,
                    completed_at_ms=None,
                )
            )
            stored_event, created = await event_repo.create_or_get_by_dedupe(
                source=event.source,
                dedupe_key=dedupe_key,
                event=event,
                dedupe_window_ms=1_000,
            )
            assert created is True
            await tool_call_repo.create(tool_call)
            await task_repo.create(task)
            await run_packet_repo.create(
                packet.model_copy(update={"event": stored_event}, deep=True)
            )

        service = MaintenanceService(
            settings=settings,
            clock=_FixedClock(now=now_ms),
            uow_factory=uow_factory,
            event_repo=lambda session: SqlAlchemyEventRepo(cast(AsyncSession, session)),
            run_packet_repo=lambda session: SqlAlchemyRunPacketRepo(
                cast(AsyncSession, session),
                settings=settings,
                memory_repo=SqlAlchemyMemoryRepo(cast(AsyncSession, session)),
            ),
            memory_repo=lambda session: SqlAlchemyMemoryRepo(cast(AsyncSession, session)),
            task_repo=lambda session: SqlAlchemyTaskRepo(cast(AsyncSession, session)),
        )

        outcome = await service.run_once(now_ms=now_ms)

        assert outcome.compacted_run_packets == 0
        assert outcome.pruned_memory_items == 1
        assert outcome.archived_tasks == 1
        assert outcome.pruned_expired_dedupe_keys == 1

        uow = uow_factory()
        async with uow:
            session = cast(AsyncSession, uow.session)
            event_repo = SqlAlchemyEventRepo(session)
            memory_repo = SqlAlchemyMemoryRepo(session)
            task_repo = SqlAlchemyTaskRepo(session)

            assert await memory_repo.list_recent(limit=10, offset=0) == []

            archived_task = await task_repo.get(task.task_id)
            assert archived_task is not None
            assert archived_task.status == TaskStatus.ARCHIVED

            assert (
                await event_repo.get_by_dedupe(
                    source=event.source,
                    dedupe_key=dedupe_key,
                    active_at_ms=now_ms,
                )
            ) is None


@pytest.mark.asyncio
async def test_maintenance_service_only_compacts_missing_or_legacy_memory(tmp_path) -> None:
    now_ms = 10 * _MS_PER_DAY
    settings = ReflexorSettings(
        workspace_root=tmp_path,
        max_run_packet_bytes=50_000,
        maintenance_batch_size=10,
        memory_compaction_after_days=1,
        memory_retention_days=None,
        archive_terminal_tasks_after_days=None,
    )

    async with _in_memory_session_factory() as session_factory:

        def uow_factory() -> SqlAlchemyUnitOfWork:
            return SqlAlchemyUnitOfWork(session_factory)

        current_run_id = _uuid()
        missing_run_id = _uuid()
        legacy_run_id = _uuid()

        current_event = Event(
            event_id=_uuid(),
            type="ticket.created",
            source="tests",
            received_at_ms=1_000,
            payload={"ticket_id": "T-current"},
        )
        missing_event = Event(
            event_id=_uuid(),
            type="ticket.updated",
            source="tests",
            received_at_ms=1_100,
            payload={"ticket_id": "T-missing"},
        )
        legacy_event = Event(
            event_id=_uuid(),
            type="ticket.closed",
            source="tests",
            received_at_ms=1_200,
            payload={"ticket_id": "T-legacy"},
        )

        uow = uow_factory()
        async with uow:
            session = cast(AsyncSession, uow.session)
            run_repo = SqlAlchemyRunRepo(session)
            event_repo = SqlAlchemyEventRepo(session)
            memory_repo = SqlAlchemyMemoryRepo(session)
            current_packet_repo = SqlAlchemyRunPacketRepo(
                session,
                settings=settings,
                memory_repo=memory_repo,
            )
            raw_packet_repo = SqlAlchemyRunPacketRepo(session, settings=settings)

            for run_id in (current_run_id, missing_run_id, legacy_run_id):
                await run_repo.create(
                    RunRecord(
                        run_id=run_id,
                        parent_run_id=None,
                        created_at_ms=1_000,
                        started_at_ms=None,
                        completed_at_ms=None,
                    )
                )

            await event_repo.create(current_event)
            await event_repo.create(missing_event)
            await event_repo.create(legacy_event)

            await current_packet_repo.create(
                RunPacket(
                    run_id=current_run_id,
                    event=current_event,
                    created_at_ms=1_000,
                )
            )
            await raw_packet_repo.create(
                RunPacket(
                    run_id=missing_run_id,
                    event=missing_event,
                    created_at_ms=1_000,
                )
            )
            await raw_packet_repo.create(
                RunPacket(
                    run_id=legacy_run_id,
                    event=legacy_event,
                    created_at_ms=1_000,
                )
            )
            await memory_repo.upsert(
                MemoryItem(
                    run_id=legacy_run_id,
                    event_id=legacy_event.event_id,
                    event_type=legacy_event.type,
                    event_source=legacy_event.source,
                    summary="legacy summary",
                    content={"event": {"event_id": legacy_event.event_id}},
                    created_at_ms=1_000,
                    updated_at_ms=1_000,
                )
            )

        service = MaintenanceService(
            settings=settings,
            clock=_FixedClock(now=now_ms),
            uow_factory=uow_factory,
            event_repo=lambda session: SqlAlchemyEventRepo(cast(AsyncSession, session)),
            run_packet_repo=lambda session: SqlAlchemyRunPacketRepo(
                cast(AsyncSession, session),
                settings=settings,
                memory_repo=SqlAlchemyMemoryRepo(cast(AsyncSession, session)),
            ),
            memory_repo=lambda session: SqlAlchemyMemoryRepo(cast(AsyncSession, session)),
            task_repo=lambda session: SqlAlchemyTaskRepo(cast(AsyncSession, session)),
        )

        outcome = await service.run_once(now_ms=now_ms)
        assert outcome.compacted_run_packets == 2
        assert outcome.pruned_memory_items == 0
        assert outcome.archived_tasks == 0
        assert outcome.pruned_expired_dedupe_keys == 0

        uow = uow_factory()
        async with uow:
            session = cast(AsyncSession, uow.session)
            memory_repo = SqlAlchemyMemoryRepo(session)

            current_memory = await memory_repo.get_by_run(current_run_id)
            missing_memory = await memory_repo.get_by_run(missing_run_id)
            legacy_memory = await memory_repo.get_by_run(legacy_run_id)

            assert current_memory is not None
            assert missing_memory is not None
            assert legacy_memory is not None
            assert current_memory.content["memory_version"] == MEMORY_SUMMARY_VERSION
            assert missing_memory.content["memory_version"] == MEMORY_SUMMARY_VERSION
            assert legacy_memory.content["memory_version"] == MEMORY_SUMMARY_VERSION
            assert missing_memory.summary != ""
            assert legacy_memory.summary != "legacy summary"

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import create_async_engine as sa_create_async_engine
from sqlalchemy.pool import StaticPool

from reflexor.config import ReflexorSettings
from reflexor.domain.enums import TaskStatus, ToolCallStatus
from reflexor.domain.models import Task, ToolCall
from reflexor.domain.models_event import Event
from reflexor.domain.models_run_packet import RunPacket
from reflexor.infra.db.engine import AsyncSessionFactory, create_async_session_factory
from reflexor.infra.db.models import (
    Base,
    EventRow,
    RunPacketRow,
    RunRow,
    TaskRow,
    ToolCallRow,
)
from reflexor.infra.db.repos import (
    SqlAlchemyEventRepo,
    SqlAlchemyRunPacketRepo,
    SqlAlchemyRunRepo,
    SqlAlchemyTaskRepo,
    SqlAlchemyToolCallRepo,
)
from reflexor.infra.db.unit_of_work import SqlAlchemyUnitOfWork
from reflexor.orchestrator.persistence import OrchestratorPersistence, OrchestratorRepoFactory
from reflexor.storage.ports import RunRecord


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
    return str(uuid.uuid4())


def _persistence(
    session_factory: AsyncSessionFactory, *, settings: ReflexorSettings
) -> OrchestratorPersistence:
    def uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(session_factory)

    repos = OrchestratorRepoFactory(
        event_repo=lambda session: SqlAlchemyEventRepo(cast(AsyncSession, session)),
        run_repo=lambda session: SqlAlchemyRunRepo(cast(AsyncSession, session)),
        tool_call_repo=lambda session: SqlAlchemyToolCallRepo(cast(AsyncSession, session)),
        task_repo=lambda session: SqlAlchemyTaskRepo(cast(AsyncSession, session)),
        run_packet_repo=lambda session: SqlAlchemyRunPacketRepo(
            cast(AsyncSession, session), settings=settings
        ),
    )

    return OrchestratorPersistence(
        uow_factory=uow_factory, repos=repos, queued_status=TaskStatus.QUEUED
    )


async def _count_rows(session: AsyncSession, row_type: type[Base]) -> int:
    return int((await session.execute(select(func.count()).select_from(row_type))).scalar_one())


@pytest.mark.asyncio
async def test_orchestrator_persistence_persists_all_artifacts_across_stages() -> None:
    settings = ReflexorSettings(max_tool_output_bytes=200, max_run_packet_bytes=10_000)

    async with _in_memory_session_factory() as session_factory:
        persistence = _persistence(session_factory, settings=settings)

        run_id = _uuid()
        event_id = _uuid()
        tool_call_id = _uuid()
        task_id = _uuid()

        event = Event(
            event_id=event_id,
            type="webhook.received",
            source="tests",
            received_at_ms=1,
            payload={"authorization": "Bearer SUPERSECRETTOKENVALUE", "note": "ok"},
        )
        tool_call = ToolCall(
            tool_call_id=tool_call_id,
            tool_name="mock.echo",
            args={"message": "hello"},
            permission_scope="debug.echo",
            idempotency_key="k1",
            status=ToolCallStatus.SUCCEEDED,
            created_at_ms=10,
            started_at_ms=10,
            completed_at_ms=11,
        )
        task = Task(
            task_id=task_id,
            run_id=run_id,
            name="echo",
            tool_call=tool_call,
            status=TaskStatus.PENDING,
            created_at_ms=10,
        )
        packet = RunPacket(
            run_id=run_id,
            event=event,
            reflex_decision={"action": "fast_tasks"},
            tasks=[task],
            tool_results=[
                {
                    "tool_call_id": tool_call_id,
                    "message": "Bearer SUPERSECRETTOKENVALUE",
                    "output": "x" * 5_000,
                }
            ],
            created_at_ms=10,
        )

        stored_event = await persistence.persist_event_and_run(
            event=event,
            run_record=RunRecord(
                run_id=run_id,
                parent_run_id=packet.parent_run_id,
                created_at_ms=packet.created_at_ms,
                started_at_ms=packet.started_at_ms,
                completed_at_ms=packet.completed_at_ms,
            ),
        )
        assert stored_event.created is True
        await persistence.persist_tasks_and_tool_calls([task])
        await persistence.finalize_run(
            packet.model_copy(update={"event": stored_event.event}, deep=True),
            enqueued_task_ids=[task_id],
        )

        uow = SqlAlchemyUnitOfWork(session_factory)
        async with uow:
            session = cast(AsyncSession, uow.session)
            assert await _count_rows(session, EventRow) == 1
            assert await _count_rows(session, RunRow) == 1
            assert await _count_rows(session, ToolCallRow) == 1
            assert await _count_rows(session, TaskRow) == 1
            assert await _count_rows(session, RunPacketRow) == 1

            task_row = (
                await session.execute(select(TaskRow).where(TaskRow.task_id == task_id))
            ).scalar_one()
            assert task_row.status == TaskStatus.QUEUED.value

            row = (
                await session.execute(select(RunPacketRow).where(RunPacketRow.run_id == run_id))
            ).scalar_one()
            assert row.packet_version == 1

            dumped = json.dumps(row.packet, ensure_ascii=False, separators=(",", ":"))
            assert "SUPERSECRETTOKENVALUE" not in dumped
            assert "<redacted>" in dumped
            assert "<truncated>" in dumped
            assert run_id in dumped
            assert event_id in dumped
            assert task_id in dumped
            assert tool_call_id in dumped


@pytest.mark.asyncio
async def test_orchestrator_persistence_stage2_rolls_back_without_affecting_stage1() -> None:
    settings = ReflexorSettings(max_tool_output_bytes=200, max_run_packet_bytes=10_000)

    async with _in_memory_session_factory() as session_factory:
        persistence = _persistence(session_factory, settings=settings)

        run_id = _uuid()
        event = Event(
            event_id=_uuid(),
            type="webhook.received",
            source="tests",
            received_at_ms=1,
            payload={"note": "ok"},
        )
        tool_call_1 = ToolCall(
            tool_call_id=_uuid(),
            tool_name="mock.echo",
            args={"message": "hello"},
            permission_scope="debug.echo",
            idempotency_key="k1",
            status=ToolCallStatus.PENDING,
            created_at_ms=10,
        )
        tool_call_2 = ToolCall(
            tool_call_id=_uuid(),
            tool_name="mock.echo",
            args={"message": "world"},
            permission_scope="debug.echo",
            idempotency_key="k2",
            status=ToolCallStatus.PENDING,
            created_at_ms=11,
        )
        shared_task_id = _uuid()
        task_1 = Task(
            task_id=shared_task_id,
            run_id=run_id,
            name="echo-1",
            tool_call=tool_call_1,
            created_at_ms=10,
        )
        task_2 = Task(
            task_id=shared_task_id,
            run_id=run_id,
            name="echo-2",
            tool_call=tool_call_2,
            created_at_ms=10,
        )
        packet = RunPacket(
            run_id=run_id,
            event=event,
            reflex_decision={"action": "fast_tasks"},
            tasks=[task_1, task_2],
            created_at_ms=10,
        )

        stored_event = await persistence.persist_event_and_run(
            event=event,
            run_record=RunRecord(
                run_id=run_id,
                parent_run_id=packet.parent_run_id,
                created_at_ms=packet.created_at_ms,
                started_at_ms=packet.started_at_ms,
                completed_at_ms=packet.completed_at_ms,
            ),
        )
        assert stored_event.created is True

        with pytest.raises(IntegrityError):
            await persistence.persist_tasks_and_tool_calls([task_1, task_2])

        uow = SqlAlchemyUnitOfWork(session_factory)
        async with uow:
            session = cast(AsyncSession, uow.session)
            assert await _count_rows(session, EventRow) == 1
            assert await _count_rows(session, RunRow) == 1
            assert await _count_rows(session, ToolCallRow) == 0
            assert await _count_rows(session, TaskRow) == 0
            assert await _count_rows(session, RunPacketRow) == 0

            persisted = (
                await session.execute(
                    select(EventRow).where(EventRow.event_id == stored_event.event.event_id)
                )
            ).scalar_one()
            assert persisted.event_id == stored_event.event.event_id


@pytest.mark.asyncio
async def test_orchestrator_persistence_dedupe_skips_second_run_using_trusted_time() -> None:
    settings = ReflexorSettings(max_tool_output_bytes=200, max_run_packet_bytes=10_000)

    async with _in_memory_session_factory() as session_factory:
        def uow_factory() -> SqlAlchemyUnitOfWork:
            return SqlAlchemyUnitOfWork(session_factory)

        repos = OrchestratorRepoFactory(
            event_repo=lambda session: SqlAlchemyEventRepo(cast(AsyncSession, session)),
            run_repo=lambda session: SqlAlchemyRunRepo(cast(AsyncSession, session)),
            tool_call_repo=lambda session: SqlAlchemyToolCallRepo(cast(AsyncSession, session)),
            task_repo=lambda session: SqlAlchemyTaskRepo(cast(AsyncSession, session)),
            run_packet_repo=lambda session: SqlAlchemyRunPacketRepo(
                cast(AsyncSession, session), settings=settings
            ),
        )
        persistence = OrchestratorPersistence(
            uow_factory=uow_factory,
            repos=repos,
            event_dedupe_window_ms=1_000,
        )

        first = Event(
            event_id=_uuid(),
            type="webhook.received",
            source="tests",
            received_at_ms=10,
            payload={"seq": 1},
            dedupe_key="ticket:T-1",
        )
        second = Event(
            event_id=_uuid(),
            type="webhook.received",
            source="tests",
            received_at_ms=50_000,
            payload={"seq": 2},
            dedupe_key="ticket:T-1",
        )

        first_result = await persistence.persist_event_and_run(
            event=first,
            run_record=RunRecord(
                run_id=_uuid(),
                parent_run_id=None,
                created_at_ms=100,
                started_at_ms=None,
                completed_at_ms=None,
            ),
        )
        second_result = await persistence.persist_event_and_run(
            event=second,
            run_record=RunRecord(
                run_id=_uuid(),
                parent_run_id=None,
                created_at_ms=150,
                started_at_ms=None,
                completed_at_ms=None,
            ),
        )

        assert first_result.created is True
        assert second_result.created is False
        assert second_result.event.event_id == first_result.event.event_id

        uow = SqlAlchemyUnitOfWork(session_factory)
        async with uow:
            session = cast(AsyncSession, uow.session)
            assert await _count_rows(session, EventRow) == 1
            assert await _count_rows(session, RunRow) == 1

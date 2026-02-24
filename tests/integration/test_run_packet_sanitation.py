from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import create_async_engine as sa_create_async_engine

from reflexor.config import ReflexorSettings
from reflexor.domain.enums import TaskStatus, ToolCallStatus
from reflexor.domain.models import Task, ToolCall
from reflexor.domain.models_event import Event
from reflexor.domain.models_run_packet import RunPacket
from reflexor.infra.db.engine import AsyncSessionFactory, create_async_session_factory
from reflexor.infra.db.models import Base, RunPacketRow
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


def _uuid() -> str:
    return str(uuid.uuid4())


@asynccontextmanager
async def _sqlite_file_session_factory(
    tmp_path: Path,
) -> AsyncIterator[tuple[AsyncSessionFactory, Path]]:
    db_path = tmp_path / "reflexor_sanitize_test.db"
    database_url = f"sqlite+aiosqlite:///{db_path}"

    engine = sa_create_async_engine(database_url, connect_args={"check_same_thread": False})
    session_factory = create_async_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield session_factory, db_path
    finally:
        await engine.dispose()
        db_path.unlink(missing_ok=True)


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

    return OrchestratorPersistence(uow_factory=uow_factory, repos=repos)


@pytest.mark.asyncio
async def test_run_packet_sanitation_is_persisted_in_db(tmp_path: Path) -> None:
    secret = "SUPERSECRETTOKENVALUE"
    settings = ReflexorSettings(
        workspace_root=tmp_path,
        max_tool_output_bytes=200,
        max_run_packet_bytes=10_000,
    )

    async with _sqlite_file_session_factory(tmp_path) as (session_factory, db_path):
        assert db_path.exists()

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
            payload={
                "authorization": f"Bearer {secret}",
                "note": f"Bearer {secret}",
            },
        )
        tool_call = ToolCall(
            tool_call_id=tool_call_id,
            tool_name="tests.mock",
            args={"note": f"Bearer {secret}"},
            permission_scope="net.http",
            idempotency_key="k1",
            status=ToolCallStatus.PENDING,
            created_at_ms=10,
        )
        task = Task(
            task_id=task_id,
            run_id=run_id,
            name="mock",
            status=TaskStatus.PENDING,
            tool_call=tool_call,
            created_at_ms=10,
        )

        packet = RunPacket(
            run_id=run_id,
            event=event,
            reflex_decision={
                "message": f"Bearer {secret}",
                "token": secret,
            },
            plan={
                "note": f"Bearer {secret}",
                "authorization": f"Bearer {secret}",
            },
            tasks=[task],
            tool_results=[
                {
                    "tool_call_id": tool_call_id,
                    "authorization": f"Bearer {secret}",
                    "output": "x" * 5_000,
                    "note": f"Bearer {secret}",
                }
            ],
            created_at_ms=10,
        )

        stored_event = await persistence.persist_event_and_run(
            event=event,
            run_record=RunRecord(
                run_id=run_id,
                parent_run_id=None,
                created_at_ms=packet.created_at_ms,
                started_at_ms=packet.started_at_ms,
                completed_at_ms=packet.completed_at_ms,
            ),
        )
        await persistence.persist_tasks_and_tool_calls([task])
        await persistence.finalize_run(
            packet.model_copy(update={"event": stored_event}, deep=True),
            enqueued_task_ids=[task_id],
        )

        uow = SqlAlchemyUnitOfWork(session_factory)
        async with uow:
            session = cast(AsyncSession, uow.session)
            row = await session.get(RunPacketRow, run_id)
            assert row is not None

            dumped = json.dumps(row.packet, ensure_ascii=False, separators=(",", ":"))
            assert secret not in dumped
            assert "<redacted>" in dumped
            assert "<truncated>" in dumped
            assert run_id in dumped
            assert event_id in dumped
            assert task_id in dumped
            assert tool_call_id in dumped

    assert not db_path.exists()

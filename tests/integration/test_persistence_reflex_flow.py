from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import create_async_engine as sa_create_async_engine

from reflexor.config import ReflexorSettings
from reflexor.domain.enums import TaskStatus, ToolCallStatus
from reflexor.domain.models_event import Event
from reflexor.infra.db.engine import AsyncSessionFactory, create_async_session_factory
from reflexor.infra.db.models import Base, EventRow, RunPacketRow, RunRow, TaskRow, ToolCallRow
from reflexor.infra.db.repos import (
    SqlAlchemyEventRepo,
    SqlAlchemyRunPacketRepo,
    SqlAlchemyRunRepo,
    SqlAlchemyTaskRepo,
    SqlAlchemyToolCallRepo,
)
from reflexor.infra.db.unit_of_work import SqlAlchemyUnitOfWork
from reflexor.infra.queue.in_memory_queue import InMemoryQueue
from reflexor.orchestrator.budgets import BudgetLimits
from reflexor.orchestrator.clock import Clock
from reflexor.orchestrator.engine import OrchestratorEngine
from reflexor.orchestrator.interfaces import NoOpPlanner
from reflexor.orchestrator.persistence import OrchestratorPersistence, OrchestratorRepoFactory
from reflexor.orchestrator.reflex_rules import RuleBasedReflexRouter
from reflexor.tools.registry import ToolRegistry
from reflexor.tools.sdk import ToolContext, ToolManifest, ToolResult


def _uuid() -> str:
    return str(uuid.uuid4())


@dataclass(slots=True)
class _FixedClock(Clock):
    now: int = 123
    monotonic: int = 0

    def now_ms(self) -> int:
        return self.now

    def monotonic_ms(self) -> int:
        return self.monotonic

    async def sleep(self, seconds: float) -> None:
        _ = seconds
        raise AssertionError("sleep should not be called in this test")


class _MockArgs(BaseModel):
    url: str


class _MockTool:
    manifest = ToolManifest(
        name="tests.mock",
        version="0.1.0",
        description="Mock tool for integration tests.",
        permission_scope="net.http",
        idempotent=True,
        max_output_bytes=10_000,
    )
    ArgsModel = _MockArgs

    async def run(self, args: _MockArgs, ctx: ToolContext) -> ToolResult:  # pragma: no cover
        _ = (args, ctx)
        return ToolResult(ok=True, data={"ok": True})


@asynccontextmanager
async def _sqlite_file_session_factory(
    tmp_path: Path,
) -> AsyncIterator[tuple[AsyncSessionFactory, Path]]:
    db_path = tmp_path / "reflexor_test.db"
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
async def test_event_to_reflex_persists_db_rows_and_enqueues(tmp_path: Path) -> None:
    settings = ReflexorSettings(workspace_root=tmp_path, max_run_packet_bytes=10_000)

    async with _sqlite_file_session_factory(tmp_path) as (session_factory, db_path):
        assert db_path.exists()

        persistence = _persistence(session_factory, settings=settings)

        registry = ToolRegistry()
        registry.register(_MockTool())

        router = RuleBasedReflexRouter.from_raw_rules(
            [
                {
                    "rule_id": "mock_rule",
                    "match": {"event_type": "webhook"},
                    "action": {
                        "kind": "fast_tool",
                        "tool_name": "tests.mock",
                        "args_template": {"url": "${payload.url}"},
                    },
                }
            ]
        )

        clock = _FixedClock()
        queue = InMemoryQueue(now_ms=clock.now_ms)

        engine = OrchestratorEngine(
            reflex_router=router,
            planner=NoOpPlanner(),
            tool_registry=registry,
            queue=queue,
            persistence=persistence,
            limits=BudgetLimits(max_tasks_per_run=10, max_tool_calls_per_run=10),
            clock=clock,
        )

        event_id = _uuid()
        event = Event(
            event_id=event_id,
            type="webhook",
            source="tests",
            received_at_ms=0,
            payload={"url": "https://example.com/path"},
        )
        run_id = await engine.handle_event(event)

        lease = await queue.dequeue(wait_s=0.0)
        assert lease is not None
        envelope = lease.envelope
        assert envelope.run_id == run_id
        assert envelope.payload is not None
        tool_call_id = str(envelope.payload["tool_call_id"])
        await queue.ack(lease)

        uow = SqlAlchemyUnitOfWork(session_factory)
        async with uow:
            session = cast(AsyncSession, uow.session)

            assert await session.get(EventRow, event_id) is not None
            assert await session.get(RunRow, run_id) is not None

            tool_call_row = await session.get(ToolCallRow, tool_call_id)
            assert tool_call_row is not None
            assert tool_call_row.permission_scope == "net.http"
            assert tool_call_row.status == ToolCallStatus.PENDING.value

            task_row = await session.get(TaskRow, envelope.task_id)
            assert task_row is not None
            assert task_row.status == TaskStatus.QUEUED.value
            assert task_row.tool_call_id == tool_call_id

            packet_row = await session.get(RunPacketRow, run_id)
            assert packet_row is not None
            assert packet_row.run_id == run_id

            # Ensure the packet blob references the run.
            stmt = select(RunPacketRow.run_id).where(RunPacketRow.run_id == run_id).limit(1)
            assert (await session.execute(stmt)).scalar_one() == run_id

    assert not db_path.exists()

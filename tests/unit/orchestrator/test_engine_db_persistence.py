from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import create_async_engine as sa_create_async_engine
from sqlalchemy.pool import StaticPool

from reflexor.config import ReflexorSettings
from reflexor.domain.enums import TaskStatus
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
        description="Mock tool for DB persistence wiring tests.",
        permission_scope="net.http",
        idempotent=True,
        max_output_bytes=10_000,
    )
    ArgsModel = _MockArgs

    async def run(self, args: _MockArgs, ctx: ToolContext) -> ToolResult:  # pragma: no cover
        _ = (args, ctx)
        return ToolResult(ok=True, data={"ok": True})


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

    return OrchestratorPersistence(uow_factory=uow_factory, repos=repos)


async def _count_rows(session: AsyncSession, row_type: type[Base]) -> int:
    return int((await session.execute(select(func.count()).select_from(row_type))).scalar_one())


def _event(*, tmp_path: Path, event_id: str) -> Event:
    _ = tmp_path
    return Event(
        event_id=event_id,
        type="webhook",
        source="tests",
        received_at_ms=0,
        payload={"url": "https://example.com/path"},
    )


@pytest.mark.asyncio
async def test_handle_event_persists_rows_and_marks_task_queued(tmp_path: Path) -> None:
    settings = ReflexorSettings(workspace_root=tmp_path, max_run_packet_bytes=10_000)

    async with _in_memory_session_factory() as session_factory:
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

        event = _event(tmp_path=tmp_path, event_id=_uuid())
        run_id = await engine.handle_event(event)

        lease = await queue.dequeue(wait_s=0.0)
        assert lease is not None
        await queue.ack(lease)

        uow = SqlAlchemyUnitOfWork(session_factory)
        async with uow:
            session = cast(AsyncSession, uow.session)
            assert await _count_rows(session, EventRow) == 1
            assert await _count_rows(session, RunRow) == 1
            assert await _count_rows(session, ToolCallRow) == 1
            assert await _count_rows(session, TaskRow) == 1
            assert await _count_rows(session, RunPacketRow) == 1

            task_row = (
                await session.execute(select(TaskRow).where(TaskRow.run_id == run_id))
            ).scalar_one()
            assert task_row.status == TaskStatus.QUEUED.value

            packet_row = (
                await session.execute(select(RunPacketRow).where(RunPacketRow.run_id == run_id))
            ).scalar_one()
            assert packet_row.packet["reflex_decision"]["action"] == "fast_tasks"
            assert packet_row.packet["reflex_decision"]["reason"] == "mock_rule"


@pytest.mark.asyncio
async def test_invalid_tasks_persist_run_packet_but_do_not_enqueue_or_store_tasks(
    tmp_path: Path,
) -> None:
    settings = ReflexorSettings(workspace_root=tmp_path, max_run_packet_bytes=10_000)

    async with _in_memory_session_factory() as session_factory:
        persistence = _persistence(session_factory, settings=settings)

        registry = ToolRegistry()
        registry.register(_MockTool())

        router = RuleBasedReflexRouter.from_raw_rules(
            [
                {
                    "rule_id": "unknown_tool",
                    "match": {"event_type": "webhook"},
                    "action": {
                        "kind": "fast_tool",
                        "tool_name": "tests.unknown",
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

        event = _event(tmp_path=tmp_path, event_id=_uuid())
        run_id = await engine.handle_event(event)

        assert await queue.dequeue(wait_s=0.0) is None

        uow = SqlAlchemyUnitOfWork(session_factory)
        async with uow:
            session = cast(AsyncSession, uow.session)
            assert await _count_rows(session, EventRow) == 1
            assert await _count_rows(session, RunRow) == 1
            assert await _count_rows(session, ToolCallRow) == 0
            assert await _count_rows(session, TaskRow) == 0
            assert await _count_rows(session, RunPacketRow) == 1

            packet_row = (
                await session.execute(select(RunPacketRow).where(RunPacketRow.run_id == run_id))
            ).scalar_one()
            assert packet_row.packet["policy_decisions"][0]["type"] == "plan_validation_error"

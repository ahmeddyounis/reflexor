from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import create_async_engine as sa_create_async_engine
from sqlalchemy.pool import StaticPool

from reflexor.domain.enums import ApprovalStatus, TaskStatus, ToolCallStatus
from reflexor.domain.models import Approval, Task, ToolCall
from reflexor.domain.models_event import Event
from reflexor.domain.models_run_packet import RunPacket
from reflexor.infra.db.engine import AsyncSessionFactory, create_async_session_factory
from reflexor.infra.db.models import Base
from reflexor.infra.db.repos import (
    SqlAlchemyApprovalRepo,
    SqlAlchemyEventRepo,
    SqlAlchemyRunPacketRepo,
    SqlAlchemyRunRepo,
    SqlAlchemyTaskRepo,
    SqlAlchemyToolCallRepo,
)
from reflexor.infra.db.unit_of_work import SqlAlchemyUnitOfWork
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


@pytest.mark.asyncio
async def test_sqlalchemy_repos_crud_status_and_pagination() -> None:
    async with _in_memory_session_factory() as session_factory:
        run_id = _uuid()
        run2_id = _uuid()
        run = RunRecord(
            run_id=run_id,
            parent_run_id=None,
            created_at_ms=1,
            started_at_ms=None,
            completed_at_ms=None,
        )
        run2 = RunRecord(
            run_id=run2_id,
            parent_run_id=run_id,
            created_at_ms=2,
            started_at_ms=None,
            completed_at_ms=None,
        )

        event1 = Event(
            event_id=_uuid(),
            type="ticket.created",
            source="tests",
            received_at_ms=10,
            payload={"ticket_id": "T-1"},
        )
        event2 = Event(
            event_id=_uuid(),
            type="ticket.updated",
            source="tests",
            received_at_ms=20,
            payload={"ticket_id": "T-1"},
        )

        tool_call1 = ToolCall(
            tool_call_id=_uuid(),
            tool_name="mock.echo",
            args={"message": "hello"},
            permission_scope="debug.echo",
            idempotency_key="k1",
            status=ToolCallStatus.PENDING,
            created_at_ms=100,
        )
        tool_call2 = ToolCall(
            tool_call_id=_uuid(),
            tool_name="mock.echo",
            args={"message": "world"},
            permission_scope="debug.echo",
            idempotency_key="k2",
            status=ToolCallStatus.PENDING,
            created_at_ms=200,
        )

        task1 = Task(
            task_id=_uuid(),
            run_id=run_id,
            name="echo-1",
            status=TaskStatus.PENDING,
            tool_call=tool_call1,
            created_at_ms=1_000,
        )
        task2 = Task(
            task_id=_uuid(),
            run_id=run_id,
            name="echo-2",
            status=TaskStatus.PENDING,
            tool_call=tool_call2,
            created_at_ms=2_000,
        )

        approval1 = Approval(
            approval_id=_uuid(),
            run_id=run_id,
            task_id=task1.task_id,
            tool_call_id=tool_call1.tool_call_id,
            status=ApprovalStatus.PENDING,
            created_at_ms=3_000,
            preview="approve this",
            payload_hash="hash-1",
        )
        approval2 = Approval(
            approval_id=_uuid(),
            run_id=run_id,
            task_id=task2.task_id,
            tool_call_id=tool_call2.tool_call_id,
            status=ApprovalStatus.PENDING,
            created_at_ms=4_000,
            preview="approve that",
            payload_hash="hash-2",
        )

        packet1 = RunPacket(
            run_id=run_id,
            parent_run_id=None,
            event=event1,
            reflex_decision={"action": "fast_tasks"},
            tasks=[task1, task2],
            created_at_ms=5_000,
        )
        packet2 = RunPacket(
            run_id=run2_id,
            parent_run_id=run_id,
            event=event2,
            reflex_decision={"action": "drop"},
            tasks=[],
            created_at_ms=6_000,
        )

        uow = SqlAlchemyUnitOfWork(session_factory)
        async with uow:
            session = cast(AsyncSession, uow.session)

            run_repo = SqlAlchemyRunRepo(session)
            event_repo = SqlAlchemyEventRepo(session)
            tool_call_repo = SqlAlchemyToolCallRepo(session)
            task_repo = SqlAlchemyTaskRepo(session)
            approval_repo = SqlAlchemyApprovalRepo(session)
            run_packet_repo = SqlAlchemyRunPacketRepo(session)

            assert await run_repo.create(run) == run
            assert await run_repo.create(run2) == run2

            created_event1 = await event_repo.create(event1)
            created_event2 = await event_repo.create(event2)
            assert created_event1.model_dump(mode="json") == event1.model_dump(mode="json")
            assert created_event2.model_dump(mode="json") == event2.model_dump(mode="json")

            created_tool_call1 = await tool_call_repo.create(tool_call1)
            assert created_tool_call1.model_dump(mode="json") == tool_call1.model_dump(mode="json")

            created_task1 = await task_repo.create(task1)
            created_task2 = await task_repo.create(task2)
            assert created_task1.model_dump(mode="json") == task1.model_dump(mode="json")
            assert created_task2.model_dump(mode="json") == task2.model_dump(mode="json")

            assert await approval_repo.create(approval1) == approval1
            assert await approval_repo.create(approval2) == approval2

            assert await run_packet_repo.create(packet1) == packet1
            assert await run_packet_repo.create(packet2) == packet2

        uow2 = SqlAlchemyUnitOfWork(session_factory)
        async with uow2:
            session = cast(AsyncSession, uow2.session)
            run_repo = SqlAlchemyRunRepo(session)
            event_repo = SqlAlchemyEventRepo(session)
            tool_call_repo = SqlAlchemyToolCallRepo(session)
            task_repo = SqlAlchemyTaskRepo(session)
            approval_repo = SqlAlchemyApprovalRepo(session)
            run_packet_repo = SqlAlchemyRunPacketRepo(session)

            assert await run_repo.get(run_id) == run
            assert await run_repo.list_recent(limit=10, offset=0) == [run2, run]

            listed_events = await event_repo.list(limit=10, offset=0)
            assert [event.event_id for event in listed_events] == [event1.event_id, event2.event_id]

            stored_task1 = await task_repo.get(task1.task_id)
            assert stored_task1 is not None
            assert stored_task1.tool_call is not None

            stored_tool_call1 = await tool_call_repo.get(tool_call1.tool_call_id)
            assert stored_tool_call1 is not None
            assert stored_tool_call1.tool_call_id == tool_call1.tool_call_id

            stored_by_key = await tool_call_repo.get_by_idempotency_key(tool_call1.idempotency_key)
            assert stored_by_key is not None
            assert stored_by_key.tool_call_id == tool_call1.tool_call_id

            listed_tool_calls = await tool_call_repo.list(limit=1, offset=0)
            assert [item.tool_call_id for item in listed_tool_calls] == [tool_call1.tool_call_id]
            paged_tool_calls = await tool_call_repo.list(limit=1, offset=1)
            assert [item.tool_call_id for item in paged_tool_calls] == [tool_call2.tool_call_id]

            listed_tasks = await task_repo.list(limit=10, offset=0, run_id=run_id)
            assert [item.task_id for item in listed_tasks] == [task1.task_id, task2.task_id]
            assert all(item.tool_call is not None for item in listed_tasks)

            stored_approval = await approval_repo.get_by_tool_call(tool_call1.tool_call_id)
            assert stored_approval is not None
            assert stored_approval.approval_id == approval1.approval_id

            listed_approvals = await approval_repo.list(limit=1, offset=1)
            assert [item.approval_id for item in listed_approvals] == [approval2.approval_id]

            updated_tool_call = await tool_call_repo.update_status(
                tool_call1.tool_call_id, ToolCallStatus.SUCCEEDED
            )
            assert updated_tool_call.status == ToolCallStatus.SUCCEEDED

            updated_task = await task_repo.update_status(task1.task_id, TaskStatus.SUCCEEDED)
            assert updated_task.status == TaskStatus.SUCCEEDED

            updated_approval = await approval_repo.update_status(
                approval1.approval_id,
                ApprovalStatus.APPROVED,
                decided_at_ms=3_000,
                decided_by="  tester  ",
            )
            assert updated_approval.status == ApprovalStatus.APPROVED
            assert updated_approval.decided_at_ms == 3_000
            assert updated_approval.decided_by == "tester"

            packet = await run_packet_repo.get(run_id)
            assert packet is not None
            assert packet.model_dump(mode="json") == packet1.model_dump(mode="json")
            recent_packets = await run_packet_repo.list_recent(limit=10, offset=0)
            assert recent_packets[0].run_id == run2_id


@pytest.mark.asyncio
async def test_task_repo_create_requires_existing_run() -> None:
    async with _in_memory_session_factory() as session_factory:
        task = Task(
            task_id=_uuid(),
            run_id=_uuid(),
            name="missing-run",
            created_at_ms=0,
        )

        uow = SqlAlchemyUnitOfWork(session_factory)
        async with uow:
            session = cast(AsyncSession, uow.session)
            task_repo = SqlAlchemyTaskRepo(session)
            with pytest.raises(KeyError, match="unknown run_id"):
                await task_repo.create(task)


@pytest.mark.asyncio
async def test_approval_repo_rejects_unsupported_status_update() -> None:
    async with _in_memory_session_factory() as session_factory:
        uow = SqlAlchemyUnitOfWork(session_factory)
        async with uow:
            session = cast(AsyncSession, uow.session)
            approval_repo = SqlAlchemyApprovalRepo(session)
            with pytest.raises(ValueError, match="unsupported approval status"):
                await approval_repo.update_status(_uuid(), ApprovalStatus.EXPIRED)

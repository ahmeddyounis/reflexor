from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import dialect as sqlite_dialect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import create_async_engine as sa_create_async_engine
from sqlalchemy.pool import StaticPool

from reflexor.config import ReflexorSettings
from reflexor.domain.enums import ApprovalStatus, RunStatus, TaskStatus, ToolCallStatus
from reflexor.domain.models import Approval, Task, ToolCall
from reflexor.domain.models_event import Event
from reflexor.domain.models_run_packet import RunPacket
from reflexor.infra.db.engine import AsyncSessionFactory, create_async_session_factory
from reflexor.infra.db.models import Base, RunPacketRow
from reflexor.infra.db.repos import (
    SqlAlchemyApprovalRepo,
    SqlAlchemyEventRepo,
    SqlAlchemyEventSuppressionRepo,
    SqlAlchemyMemoryRepo,
    SqlAlchemyRunPacketRepo,
    SqlAlchemyRunRepo,
    SqlAlchemyTaskRepo,
    SqlAlchemyToolCallRepo,
)
from reflexor.infra.db.repos.runs.summaries import _list_run_summaries_stmt
from reflexor.infra.db.repos.tasks import _task_summary_stmt
from reflexor.infra.db.unit_of_work import SqlAlchemyUnitOfWork
from reflexor.memory.models import MemoryItem
from reflexor.memory.summary import MEMORY_SUMMARY_VERSION
from reflexor.storage.ports import EventSuppressionRecord, RunRecord


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


def test_run_summary_query_projects_event_fields_without_selecting_full_packet_blob() -> None:
    stmt = _list_run_summaries_stmt(limit=10, offset=0)
    selected = {
        (getattr(column, "name", None), getattr(getattr(column, "table", None), "name", None))
        for column in stmt.selected_columns
    }

    assert ("packet", "run_packets") not in selected
    assert ("event_type", None) in selected
    assert ("event_source", None) in selected

    compiled = str(stmt.compile(dialect=sqlite_dialect(), compile_kwargs={"literal_binds": True}))
    assert "run_packets.packet" in compiled
    assert " AS event_type" in compiled
    assert " AS event_source" in compiled


def test_task_summary_query_omits_tool_call_args_from_projection() -> None:
    stmt = _task_summary_stmt(limit=10, offset=0)
    selected = {
        (getattr(column, "name", None), getattr(getattr(column, "table", None), "name", None))
        for column in stmt.selected_columns
    }

    assert ("args", "tool_calls") not in selected
    assert ("result_ref", "tool_calls") not in selected
    assert ("tool_call_id", "tool_calls") in selected
    assert ("tool_name", "tool_calls") in selected


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
            memory_repo = SqlAlchemyMemoryRepo(session)
            run_packet_repo = SqlAlchemyRunPacketRepo(session, memory_repo=memory_repo)

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
            memory_repo = SqlAlchemyMemoryRepo(session)
            run_packet_repo = SqlAlchemyRunPacketRepo(session, memory_repo=memory_repo)

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

            recent_memory = await memory_repo.list_recent(limit=10, offset=0)
            assert [item.run_id for item in recent_memory] == [run2_id, run_id]

            updated_memory = await memory_repo.list_recent(
                limit=10,
                offset=0,
                event_type="ticket.updated",
                event_source="tests",
            )
            assert [item.run_id for item in updated_memory] == [run2_id]

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

            memory_item = await memory_repo.get_by_run(run_id)
            assert memory_item is not None
            assert memory_item.run_id == run_id
            assert memory_item.event_type == event1.type
            assert memory_item.event_source == event1.source
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


@pytest.mark.asyncio
async def test_event_suppression_repo_validates_and_normalizes_records() -> None:
    async with _in_memory_session_factory() as session_factory:
        uow = SqlAlchemyUnitOfWork(session_factory)
        async with uow:
            session = cast(AsyncSession, uow.session)
            repo = SqlAlchemyEventSuppressionRepo(session)

            with pytest.raises(ValueError, match="count must be >= 0"):
                await repo.upsert(
                    EventSuppressionRecord(
                        signature_hash="sig-1",
                        event_type="webhook",
                        event_source="tests",
                        signature={},
                        window_start_ms=0,
                        count=-1,
                        threshold=2,
                        window_ms=60_000,
                        suppressed_until_ms=1_000,
                        resume_required=False,
                        cleared_at_ms=None,
                        cleared_by=None,
                        cleared_request_id=None,
                        created_at_ms=0,
                        updated_at_ms=0,
                        expires_at_ms=1_000,
                    )
                )

            stored = await repo.upsert(
                EventSuppressionRecord(
                    signature_hash=" sig-1 ",
                    event_type=" webhook ",
                    event_source=" tests ",
                    signature={"ticket": "T-1"},
                    window_start_ms=0,
                    count=3,
                    threshold=2,
                    window_ms=60_000,
                    suppressed_until_ms=61_000,
                    resume_required=False,
                    cleared_at_ms=100,
                    cleared_by=" operator@example.com ",
                    cleared_request_id=" request-1 ",
                    created_at_ms=0,
                    updated_at_ms=100,
                    expires_at_ms=61_000,
                )
            )

            assert stored.signature_hash == "sig-1"
            assert stored.event_type == "webhook"
            assert stored.event_source == "tests"
            assert stored.cleared_by == "operator@example.com"
            assert stored.cleared_request_id == "request-1"


@pytest.mark.asyncio
async def test_approval_repo_status_updates_are_terminal_and_idempotent() -> None:
    async with _in_memory_session_factory() as session_factory:
        run_id = _uuid()
        tool_call = ToolCall(
            tool_call_id=_uuid(),
            tool_name="mock.echo",
            args={"message": "hello"},
            permission_scope="debug.echo",
            idempotency_key="k1",
            status=ToolCallStatus.PENDING,
            created_at_ms=100,
        )
        task = Task(
            task_id=_uuid(),
            run_id=run_id,
            name="needs-approval",
            status=TaskStatus.WAITING_APPROVAL,
            tool_call=tool_call,
            created_at_ms=1_000,
        )
        approval = Approval(
            approval_id=_uuid(),
            run_id=run_id,
            task_id=task.task_id,
            tool_call_id=tool_call.tool_call_id,
            status=ApprovalStatus.PENDING,
            created_at_ms=2_000,
            preview="approve this",
            payload_hash="hash-1",
        )

        uow = SqlAlchemyUnitOfWork(session_factory)
        async with uow:
            session = cast(AsyncSession, uow.session)
            run_repo = SqlAlchemyRunRepo(session)
            task_repo = SqlAlchemyTaskRepo(session)
            approval_repo = SqlAlchemyApprovalRepo(session)

            await run_repo.create(
                RunRecord(
                    run_id=run_id,
                    parent_run_id=None,
                    created_at_ms=1,
                    started_at_ms=None,
                    completed_at_ms=None,
                )
            )
            await task_repo.create(task)
            await approval_repo.create(approval)

            approved = await approval_repo.update_status(
                approval.approval_id,
                ApprovalStatus.APPROVED,
                decided_at_ms=3_000,
                decided_by="tester",
            )
            approved_again = await approval_repo.update_status(
                approval.approval_id,
                ApprovalStatus.APPROVED,
                decided_at_ms=4_000,
                decided_by="other",
            )

            assert approved_again == approved

            with pytest.raises(ValueError, match="already been decided as approved"):
                await approval_repo.update_status(approval.approval_id, ApprovalStatus.DENIED)


@pytest.mark.asyncio
async def test_event_repo_create_or_get_by_dedupe_is_idempotent() -> None:
    async with _in_memory_session_factory() as session_factory:
        dedupe_key = "ticket:T-1"
        source = "tests"
        event1 = Event(
            event_id=_uuid(),
            type="ticket.created",
            source=source,
            received_at_ms=10,
            payload={"ticket_id": "T-1", "seq": 1},
            dedupe_key=dedupe_key,
        )
        event2 = Event(
            event_id=_uuid(),
            type="ticket.created",
            source=source,
            received_at_ms=20,
            payload={"ticket_id": "T-1", "seq": 2},
            dedupe_key=dedupe_key,
        )

        uow1 = SqlAlchemyUnitOfWork(session_factory)
        async with uow1:
            session = cast(AsyncSession, uow1.session)
            event_repo = SqlAlchemyEventRepo(session)
            stored1, created1 = await event_repo.create_or_get_by_dedupe(
                source=source,
                dedupe_key=dedupe_key,
                event=event1,
            )
            assert created1 is True

        uow2 = SqlAlchemyUnitOfWork(session_factory)
        async with uow2:
            session = cast(AsyncSession, uow2.session)
            event_repo = SqlAlchemyEventRepo(session)
            stored2, created2 = await event_repo.create_or_get_by_dedupe(
                source=source,
                dedupe_key=dedupe_key,
                event=event2,
            )
            assert created2 is False
            assert stored2.event_id == stored1.event_id

            listed_events = await event_repo.list(limit=10, offset=0)
            assert [event.event_id for event in listed_events] == [stored1.event_id]


@pytest.mark.asyncio
async def test_event_repo_allows_same_dedupe_key_after_window_expires() -> None:
    async with _in_memory_session_factory() as session_factory:
        dedupe_key = "ticket:T-1"
        source = "tests"
        event1 = Event(
            event_id=_uuid(),
            type="ticket.created",
            source=source,
            received_at_ms=10,
            payload={"ticket_id": "T-1", "seq": 1},
            dedupe_key=dedupe_key,
        )
        event2 = Event(
            event_id=_uuid(),
            type="ticket.created",
            source=source,
            received_at_ms=30,
            payload={"ticket_id": "T-1", "seq": 2},
            dedupe_key=dedupe_key,
        )

        uow1 = SqlAlchemyUnitOfWork(session_factory)
        async with uow1:
            session = cast(AsyncSession, uow1.session)
            event_repo = SqlAlchemyEventRepo(session)
            stored1, created1 = await event_repo.create_or_get_by_dedupe(
                source=source,
                dedupe_key=dedupe_key,
                event=event1,
                dedupe_window_ms=5,
            )
            assert created1 is True

        uow2 = SqlAlchemyUnitOfWork(session_factory)
        async with uow2:
            session = cast(AsyncSession, uow2.session)
            event_repo = SqlAlchemyEventRepo(session)

            assert (
                await event_repo.get_by_dedupe(
                    source=source,
                    dedupe_key=dedupe_key,
                    active_at_ms=12,
                )
            ) is not None
            assert (
                await event_repo.get_by_dedupe(
                    source=source,
                    dedupe_key=dedupe_key,
                    active_at_ms=15,
                )
            ) is None

            stored2, created2 = await event_repo.create_or_get_by_dedupe(
                source=source,
                dedupe_key=dedupe_key,
                event=event2,
                dedupe_window_ms=5,
            )
            assert created2 is True
            assert stored2.event_id != stored1.event_id

            listed_events = await event_repo.list(limit=10, offset=0)
            assert [event.event_id for event in listed_events] == [
                stored1.event_id,
                stored2.event_id,
            ]


@pytest.mark.asyncio
async def test_memory_repo_search_and_delete_older_than() -> None:
    settings = ReflexorSettings(max_run_packet_bytes=50_000)

    async with _in_memory_session_factory() as session_factory:
        run_old_id = _uuid()
        run_new_id = _uuid()
        event_old = Event(
            event_id=_uuid(),
            type="ticket.created",
            source="tests",
            received_at_ms=10,
            payload={"ticket_id": "T-1"},
        )
        event_new = Event(
            event_id=_uuid(),
            type="ticket.updated",
            source="tests",
            received_at_ms=20,
            payload={"ticket_id": "T-1"},
        )

        uow = SqlAlchemyUnitOfWork(session_factory)
        async with uow:
            session = cast(AsyncSession, uow.session)
            run_repo = SqlAlchemyRunRepo(session)
            event_repo = SqlAlchemyEventRepo(session)
            memory_repo = SqlAlchemyMemoryRepo(session)
            run_packet_repo = SqlAlchemyRunPacketRepo(
                session,
                settings=settings,
                memory_repo=memory_repo,
            )

            await run_repo.create(
                RunRecord(
                    run_id=run_old_id,
                    parent_run_id=None,
                    created_at_ms=100,
                    started_at_ms=None,
                    completed_at_ms=None,
                )
            )
            await run_repo.create(
                RunRecord(
                    run_id=run_new_id,
                    parent_run_id=None,
                    created_at_ms=200,
                    started_at_ms=None,
                    completed_at_ms=None,
                )
            )
            await event_repo.create(event_old)
            await event_repo.create(event_new)
            await run_packet_repo.create(
                RunPacket(
                    run_id=run_old_id,
                    event=event_old,
                    created_at_ms=100,
                )
            )
            await run_packet_repo.create(
                RunPacket(
                    run_id=run_new_id,
                    event=event_new,
                    created_at_ms=200,
                )
            )

        uow2 = SqlAlchemyUnitOfWork(session_factory)
        async with uow2:
            session = cast(AsyncSession, uow2.session)
            memory_repo = SqlAlchemyMemoryRepo(session)
            await memory_repo.upsert(
                MemoryItem(
                    run_id=run_old_id,
                    event_id=event_old.event_id,
                    event_type=event_old.type,
                    event_source=event_old.source,
                    summary="ticket.updated ticket_updated",
                    content={"kind": "literal_underscore"},
                    created_at_ms=100,
                    updated_at_ms=100,
                )
            )
            await memory_repo.upsert(
                MemoryItem(
                    run_id=run_new_id,
                    event_id=event_new.event_id,
                    event_type=event_new.type,
                    event_source=event_new.source,
                    summary="ticketXupdated",
                    content={"kind": "wildcard_candidate"},
                    created_at_ms=200,
                    updated_at_ms=200,
                )
            )

            searched = await memory_repo.search(query="ticket.updated", limit=10, offset=0)
            assert [item.run_id for item in searched] == [run_old_id]

            literal_match = await memory_repo.search(query="ticket_updated", limit=10, offset=0)
            assert [item.run_id for item in literal_match] == [run_old_id]

            content_match = await memory_repo.search(query="literal_underscore", limit=10, offset=0)
            assert [item.run_id for item in content_match] == [run_old_id]

            deleted = await memory_repo.delete_older_than(updated_before_ms=150, limit=10)
            assert deleted == 1

            remaining = await memory_repo.list_recent(limit=10, offset=0)
            assert [item.run_id for item in remaining] == [run_new_id]


@pytest.mark.asyncio
async def test_run_packet_repo_lists_only_packets_needing_memory_refresh() -> None:
    settings = ReflexorSettings(max_run_packet_bytes=50_000)

    async with _in_memory_session_factory() as session_factory:
        current_run_id = _uuid()
        missing_run_id = _uuid()
        legacy_run_id = _uuid()
        current_event = Event(
            event_id=_uuid(),
            type="ticket.created",
            source="tests",
            received_at_ms=10,
            payload={"ticket_id": "T-current"},
        )
        missing_event = Event(
            event_id=_uuid(),
            type="ticket.updated",
            source="tests",
            received_at_ms=20,
            payload={"ticket_id": "T-missing"},
        )
        legacy_event = Event(
            event_id=_uuid(),
            type="ticket.closed",
            source="tests",
            received_at_ms=30,
            payload={"ticket_id": "T-legacy"},
        )

        uow = SqlAlchemyUnitOfWork(session_factory)
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

            for run_id, created_at_ms in (
                (current_run_id, 100),
                (missing_run_id, 200),
                (legacy_run_id, 300),
            ):
                await run_repo.create(
                    RunRecord(
                        run_id=run_id,
                        parent_run_id=None,
                        created_at_ms=created_at_ms,
                        started_at_ms=None,
                        completed_at_ms=None,
                    )
                )

            await event_repo.create(current_event)
            await event_repo.create(missing_event)
            await event_repo.create(legacy_event)

            await current_packet_repo.create(
                RunPacket(run_id=current_run_id, event=current_event, created_at_ms=100)
            )
            await raw_packet_repo.create(
                RunPacket(run_id=missing_run_id, event=missing_event, created_at_ms=200)
            )
            await raw_packet_repo.create(
                RunPacket(run_id=legacy_run_id, event=legacy_event, created_at_ms=300)
            )
            await memory_repo.upsert(
                MemoryItem(
                    run_id=legacy_run_id,
                    event_id=legacy_event.event_id,
                    event_type=legacy_event.type,
                    event_source=legacy_event.source,
                    summary="legacy summary",
                    content={"event": {"event_id": legacy_event.event_id}},
                    created_at_ms=300,
                    updated_at_ms=300,
                )
            )

        uow2 = SqlAlchemyUnitOfWork(session_factory)
        async with uow2:
            session = cast(AsyncSession, uow2.session)
            run_packet_repo = SqlAlchemyRunPacketRepo(session, settings=settings)

            needing_refresh = await run_packet_repo.list_for_memory_refresh_before(
                created_before_ms=400,
                memory_version=MEMORY_SUMMARY_VERSION,
                limit=10,
            )
            assert [packet.run_id for packet in needing_refresh] == [missing_run_id, legacy_run_id]


@pytest.mark.asyncio
async def test_task_repo_archive_terminal_before_updates_run_summary() -> None:
    async with _in_memory_session_factory() as session_factory:
        run_id = _uuid()
        old_tool_call = ToolCall(
            tool_call_id=_uuid(),
            tool_name="mock.echo",
            args={"message": "old"},
            permission_scope="debug.echo",
            idempotency_key="old",
            status=ToolCallStatus.SUCCEEDED,
            created_at_ms=10,
            started_at_ms=10,
            completed_at_ms=20,
        )
        recent_tool_call = ToolCall(
            tool_call_id=_uuid(),
            tool_name="mock.echo",
            args={"message": "recent"},
            permission_scope="debug.echo",
            idempotency_key="recent",
            status=ToolCallStatus.SUCCEEDED,
            created_at_ms=30,
            started_at_ms=30,
            completed_at_ms=40,
        )
        old_task = Task(
            task_id=_uuid(),
            run_id=run_id,
            name="old",
            status=TaskStatus.SUCCEEDED,
            tool_call=old_tool_call,
            attempts=1,
            created_at_ms=10,
            started_at_ms=10,
            completed_at_ms=20,
        )
        recent_task = Task(
            task_id=_uuid(),
            run_id=run_id,
            name="recent",
            status=TaskStatus.SUCCEEDED,
            tool_call=recent_tool_call,
            attempts=1,
            created_at_ms=30,
            started_at_ms=30,
            completed_at_ms=200,
        )

        uow = SqlAlchemyUnitOfWork(session_factory)
        async with uow:
            session = cast(AsyncSession, uow.session)
            run_repo = SqlAlchemyRunRepo(session)
            task_repo = SqlAlchemyTaskRepo(session)
            tool_call_repo = SqlAlchemyToolCallRepo(session)

            await run_repo.create(
                RunRecord(
                    run_id=run_id,
                    parent_run_id=None,
                    created_at_ms=1,
                    started_at_ms=None,
                    completed_at_ms=None,
                )
            )
            await tool_call_repo.create(old_tool_call)
            await tool_call_repo.create(recent_tool_call)
            await task_repo.create(old_task)
            await task_repo.create(recent_task)

            archived_old = await task_repo.archive_terminal_before(
                completed_before_ms=100,
                limit=10,
            )
            assert archived_old == 1

            updated_old = await task_repo.get(old_task.task_id)
            updated_recent = await task_repo.get(recent_task.task_id)
            assert updated_old is not None
            assert updated_recent is not None
            assert updated_old.status == TaskStatus.ARCHIVED
            assert updated_recent.status == TaskStatus.SUCCEEDED

            summary = await run_repo.get_summary(run_id)
            assert summary is not None
            assert summary.status == RunStatus.SUCCEEDED

            archived_recent = await task_repo.archive_terminal_before(
                completed_before_ms=300,
                limit=10,
            )
            assert archived_recent == 1

            summary = await run_repo.get_summary(run_id)
            assert summary is not None
            assert summary.status == RunStatus.ARCHIVED


@pytest.mark.asyncio
async def test_run_packet_repo_persists_sanitized_packet() -> None:
    settings = ReflexorSettings(max_tool_output_bytes=200)

    async with _in_memory_session_factory() as session_factory:
        run_id = _uuid()
        run = RunRecord(
            run_id=run_id,
            parent_run_id=None,
            created_at_ms=1,
            started_at_ms=None,
            completed_at_ms=None,
        )
        event = Event(
            event_id=_uuid(),
            type="webhook.received",
            source="tests",
            received_at_ms=10,
            payload={
                "authorization": "Bearer SUPERSECRETTOKENVALUE",
                "note": "ok",
            },
        )
        tool_call = ToolCall(
            tool_call_id=_uuid(),
            tool_name="mock.echo",
            args={"message": "hello"},
            permission_scope="debug.echo",
            idempotency_key="k1",
            status=ToolCallStatus.SUCCEEDED,
            created_at_ms=100,
            started_at_ms=100,
            completed_at_ms=101,
        )
        task = Task(
            task_id=_uuid(),
            run_id=run_id,
            name="echo",
            status=TaskStatus.SUCCEEDED,
            tool_call=tool_call,
            created_at_ms=1_000,
            started_at_ms=1_000,
            completed_at_ms=1_001,
        )
        packet = RunPacket(
            run_id=run_id,
            parent_run_id=None,
            event=event,
            reflex_decision={"action": "fast_tasks"},
            tasks=[task],
            tool_results=[
                {
                    "tool_call_id": tool_call.tool_call_id,
                    "message": "Bearer SUPERSECRETTOKENVALUE",
                    "output": "x" * 5_000,
                }
            ],
            created_at_ms=5_000,
        )

        uow = SqlAlchemyUnitOfWork(session_factory)
        async with uow:
            session = cast(AsyncSession, uow.session)
            run_repo = SqlAlchemyRunRepo(session)
            run_packet_repo = SqlAlchemyRunPacketRepo(session, settings=settings)

            assert await run_repo.create(run) == run
            stored = await run_packet_repo.create(packet)

            assert stored.run_id == run_id
            assert stored.event.event_id == event.event_id
            assert stored.event.payload["authorization"] == "<redacted>"

            row = (
                await session.execute(select(RunPacketRow).where(RunPacketRow.run_id == run_id))
            ).scalar_one()
            assert row.packet_version == 1

            dumped = json.dumps(row.packet, ensure_ascii=False, separators=(",", ":"))
            assert "SUPERSECRETTOKENVALUE" not in dumped
            assert "<redacted>" in dumped
            assert "<truncated>" in dumped
            assert run_id in dumped
            assert event.event_id in dumped
            assert task.task_id in dumped
            assert tool_call.tool_call_id in dumped

            fetched = await run_packet_repo.get(run_id)
            assert fetched is not None
            assert fetched.event.payload["authorization"] == "<redacted>"


@pytest.mark.asyncio
async def test_run_packet_repo_sanitizes_memory_summary_content() -> None:
    settings = ReflexorSettings(max_tool_output_bytes=200)

    async with _in_memory_session_factory() as session_factory:
        run_id = _uuid()
        run = RunRecord(
            run_id=run_id,
            parent_run_id=None,
            created_at_ms=1,
            started_at_ms=None,
            completed_at_ms=None,
        )
        packet = RunPacket(
            run_id=run_id,
            event=Event(
                event_id=_uuid(),
                type="webhook.received",
                source="tests",
                received_at_ms=10,
                payload={"note": "ok"},
            ),
            reflex_decision={"token": "Bearer SUPERSECRETTOKENVALUE"},
            plan={"authorization": "Bearer SUPERSECRETTOKENVALUE"},
            created_at_ms=5_000,
        )

        uow = SqlAlchemyUnitOfWork(session_factory)
        async with uow:
            session = cast(AsyncSession, uow.session)
            run_repo = SqlAlchemyRunRepo(session)
            memory_repo = SqlAlchemyMemoryRepo(session)
            run_packet_repo = SqlAlchemyRunPacketRepo(
                session,
                settings=settings,
                memory_repo=memory_repo,
            )

            assert await run_repo.create(run) == run
            await run_packet_repo.create(packet)

            memory_items = await memory_repo.list_recent(limit=10, offset=0)
            assert len(memory_items) == 1
            dumped = json.dumps(memory_items[0].content, ensure_ascii=False, separators=(",", ":"))
            assert "SUPERSECRETTOKENVALUE" not in dumped
            assert "<redacted>" in dumped


@pytest.mark.asyncio
async def test_run_repo_list_summaries_filters_by_status_and_paginates() -> None:
    settings = ReflexorSettings(max_tool_output_bytes=200)

    async with _in_memory_session_factory() as session_factory:
        run_created_id = _uuid()
        run_running_id = _uuid()
        run_failed_id = _uuid()
        run_empty_id = _uuid()

        run_created = RunRecord(
            run_id=run_created_id,
            parent_run_id=None,
            created_at_ms=1_000,
            started_at_ms=None,
            completed_at_ms=None,
        )
        run_running = RunRecord(
            run_id=run_running_id,
            parent_run_id=None,
            created_at_ms=2_000,
            started_at_ms=None,
            completed_at_ms=None,
        )
        run_failed = RunRecord(
            run_id=run_failed_id,
            parent_run_id=None,
            created_at_ms=3_000,
            started_at_ms=None,
            completed_at_ms=None,
        )
        run_empty = RunRecord(
            run_id=run_empty_id,
            parent_run_id=None,
            created_at_ms=4_000,
            started_at_ms=None,
            completed_at_ms=None,
        )

        task_created = Task(
            task_id=_uuid(),
            run_id=run_created_id,
            name="queued",
            status=TaskStatus.QUEUED,
            created_at_ms=1,
        )
        task_running = Task(
            task_id=_uuid(),
            run_id=run_running_id,
            name="running",
            status=TaskStatus.RUNNING,
            created_at_ms=1,
        )
        task_failed = Task(
            task_id=_uuid(),
            run_id=run_failed_id,
            name="failed",
            status=TaskStatus.FAILED,
            created_at_ms=1,
        )

        packet_created = RunPacket(
            run_id=run_created_id,
            event=Event(
                event_id=_uuid(),
                type="evt.created",
                source="tests",
                received_at_ms=0,
                payload={},
            ),
            created_at_ms=1_000,
        )
        packet_running = RunPacket(
            run_id=run_running_id,
            event=Event(
                event_id=_uuid(),
                type="evt.running",
                source="tests",
                received_at_ms=0,
                payload={},
            ),
            created_at_ms=2_000,
        )
        packet_failed = RunPacket(
            run_id=run_failed_id,
            event=Event(
                event_id=_uuid(),
                type="evt.failed",
                source="tests",
                received_at_ms=0,
                payload={},
            ),
            created_at_ms=3_000,
        )
        packet_empty = RunPacket(
            run_id=run_empty_id,
            event=Event(
                event_id=_uuid(),
                type="evt.empty",
                source="tests",
                received_at_ms=0,
                payload={},
            ),
            created_at_ms=4_000,
        )

        uow = SqlAlchemyUnitOfWork(session_factory)
        async with uow:
            session = cast(AsyncSession, uow.session)
            run_repo = SqlAlchemyRunRepo(session)
            task_repo = SqlAlchemyTaskRepo(session)
            run_packet_repo = SqlAlchemyRunPacketRepo(session, settings=settings)

            assert await run_repo.create(run_created) == run_created
            assert await run_repo.create(run_running) == run_running
            assert await run_repo.create(run_failed) == run_failed
            assert await run_repo.create(run_empty) == run_empty

            await task_repo.create(task_created)
            await task_repo.create(task_running)
            await task_repo.create(task_failed)

            await run_packet_repo.create(packet_created)
            await run_packet_repo.create(packet_running)
            await run_packet_repo.create(packet_failed)
            await run_packet_repo.create(packet_empty)

            summaries = await run_repo.list_summaries(limit=10, offset=0)
            assert [s.run_id for s in summaries] == [
                run_empty_id,
                run_failed_id,
                run_running_id,
                run_created_id,
            ]
            assert [s.status for s in summaries] == [
                RunStatus.SUCCEEDED,
                RunStatus.FAILED,
                RunStatus.RUNNING,
                RunStatus.CREATED,
            ]
            assert summaries[0].event_type == "evt.empty"
            assert summaries[0].event_source == "tests"

            failed_only = await run_repo.list_summaries(limit=10, offset=0, status=RunStatus.FAILED)
            assert [s.run_id for s in failed_only] == [run_failed_id]

            paged = await run_repo.list_summaries(limit=1, offset=1)
            assert [s.run_id for s in paged] == [run_failed_id]

            summary = await run_repo.get_summary(run_running_id)
            assert summary is not None
            assert summary.run_id == run_running_id
            assert summary.status == RunStatus.RUNNING


@pytest.mark.asyncio
async def test_task_and_approval_list_filters_and_orders() -> None:
    async with _in_memory_session_factory() as session_factory:
        run_id = _uuid()
        run = RunRecord(
            run_id=run_id,
            parent_run_id=None,
            created_at_ms=1,
            started_at_ms=None,
            completed_at_ms=None,
        )

        tool_call = ToolCall(
            tool_call_id=_uuid(),
            tool_name="mock.echo",
            args={"message": "hello"},
            permission_scope="debug.echo",
            idempotency_key="k1",
            status=ToolCallStatus.PENDING,
            created_at_ms=100,
        )
        task_queued = Task(
            task_id=_uuid(),
            run_id=run_id,
            name="queued",
            status=TaskStatus.QUEUED,
            tool_call=tool_call,
            created_at_ms=10,
        )
        task_succeeded = Task(
            task_id=_uuid(),
            run_id=run_id,
            name="done",
            status=TaskStatus.SUCCEEDED,
            tool_call=tool_call,
            created_at_ms=20,
            started_at_ms=20,
            completed_at_ms=21,
            attempts=1,
        )

        approval_approved = Approval(
            approval_id=_uuid(),
            run_id=run_id,
            task_id=task_queued.task_id,
            tool_call_id=tool_call.tool_call_id,
            status=ApprovalStatus.APPROVED,
            created_at_ms=1,
            decided_at_ms=2,
        )
        approval_pending = Approval(
            approval_id=_uuid(),
            run_id=run_id,
            task_id=task_succeeded.task_id,
            tool_call_id=tool_call.tool_call_id,
            status=ApprovalStatus.PENDING,
            created_at_ms=3,
        )

        uow = SqlAlchemyUnitOfWork(session_factory)
        async with uow:
            session = cast(AsyncSession, uow.session)
            run_repo = SqlAlchemyRunRepo(session)
            task_repo = SqlAlchemyTaskRepo(session)
            approval_repo = SqlAlchemyApprovalRepo(session)

            assert await run_repo.create(run) == run
            await task_repo.create(task_queued)
            await task_repo.create(task_succeeded)
            await approval_repo.create(approval_approved)
            await approval_repo.create(approval_pending)

            queued_only = await task_repo.list(
                limit=10, offset=0, run_id=run_id, status=TaskStatus.QUEUED
            )
            assert [task.task_id for task in queued_only] == [task_queued.task_id]

            approvals = await approval_repo.list(limit=10, offset=0)
            assert [approval.approval_id for approval in approvals] == [
                approval_pending.approval_id,
                approval_approved.approval_id,
            ]

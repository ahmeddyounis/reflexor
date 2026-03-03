from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import create_async_engine as sa_create_async_engine
from sqlalchemy.pool import StaticPool

from reflexor.application.services import RunQueryService, TaskQueryService
from reflexor.cli.client import ApiClient, LocalClient
from reflexor.cli.commands.runs import _build_run_show_payload
from reflexor.config import ReflexorSettings
from reflexor.domain.enums import TaskStatus, ToolCallStatus
from reflexor.domain.models import Task, ToolCall
from reflexor.domain.models_event import Event
from reflexor.domain.models_run_packet import RunPacket
from reflexor.infra.db.engine import AsyncSessionFactory, create_async_session_factory
from reflexor.infra.db.models import Base
from reflexor.infra.db.repos import SqlAlchemyRunPacketRepo, SqlAlchemyRunRepo, SqlAlchemyTaskRepo
from reflexor.infra.db.unit_of_work import SqlAlchemyUnitOfWork
from reflexor.storage.ports import RunRecord
from reflexor.tools.registry import ToolRegistry


def _uuid() -> str:
    return str(uuid.uuid4())


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


def _build_local_client(
    session_factory: AsyncSessionFactory, *, settings: ReflexorSettings
) -> LocalClient:
    def uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(session_factory)

    def run_repo(session) -> SqlAlchemyRunRepo:  # type: ignore[no-untyped-def]
        return SqlAlchemyRunRepo(cast(AsyncSession, session))

    def run_packet_repo(session) -> SqlAlchemyRunPacketRepo:  # type: ignore[no-untyped-def]
        return SqlAlchemyRunPacketRepo(cast(AsyncSession, session), settings=settings)

    def task_repo(session) -> SqlAlchemyTaskRepo:  # type: ignore[no-untyped-def]
        return SqlAlchemyTaskRepo(cast(AsyncSession, session))

    class FakeSubmitter:
        async def submit_event(self, _event: Event):  # type: ignore[no-untyped-def]
            raise AssertionError("not used")

    class FakeApprovals:
        async def list_approvals(self, **_kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("not used")

        async def approve(self, _approval_id: str, *, decided_by: str | None = None):  # type: ignore[no-untyped-def]
            _ = decided_by
            raise AssertionError("not used")

        async def deny(self, _approval_id: str, *, decided_by: str | None = None):  # type: ignore[no-untyped-def]
            _ = decided_by
            raise AssertionError("not used")

    run_queries = RunQueryService(
        uow_factory=uow_factory,
        run_repo=run_repo,  # type: ignore[arg-type]
        run_packet_repo=run_packet_repo,  # type: ignore[arg-type]
    )
    task_queries = TaskQueryService(uow_factory=uow_factory, task_repo=task_repo)  # type: ignore[arg-type]

    return LocalClient(
        settings=settings,
        submitter=FakeSubmitter(),  # type: ignore[arg-type]
        run_queries=run_queries,
        task_queries=task_queries,
        approval_commands=FakeApprovals(),  # type: ignore[arg-type]
        tool_registry=ToolRegistry(),
    )


async def _seed_run(
    session_factory: AsyncSessionFactory,
    *,
    settings: ReflexorSettings,
    run_id: str,
    created_at_ms: int,
) -> None:
    run = RunRecord(
        run_id=run_id,
        parent_run_id=None,
        created_at_ms=created_at_ms,
        started_at_ms=None,
        completed_at_ms=None,
    )
    event = Event(
        event_id=_uuid(),
        type="webhook",
        source="tests",
        received_at_ms=created_at_ms,
        payload={"authorization": "Bearer SUPERSECRETTOKENVALUE", "seq": 1},
    )

    tool_call_1 = ToolCall(
        tool_call_id=_uuid(),
        tool_name="mock.echo",
        args={"message": "hello"},
        permission_scope="debug.echo",
        idempotency_key="k1",
        status=ToolCallStatus.PENDING,
        created_at_ms=created_at_ms,
    )
    task_1 = Task(
        task_id=_uuid(),
        run_id=run_id,
        name="task-1",
        status=TaskStatus.QUEUED,
        tool_call=tool_call_1,
        max_attempts=3,
        timeout_s=60,
        created_at_ms=created_at_ms,
    )
    task_2 = Task(
        task_id=_uuid(),
        run_id=run_id,
        name="task-2",
        status=TaskStatus.SUCCEEDED,
        tool_call=None,
        max_attempts=3,
        timeout_s=60,
        created_at_ms=created_at_ms + 1,
    )

    packet = RunPacket(
        run_id=run_id,
        event=event,
        tasks=[task_1, task_2],
        created_at_ms=created_at_ms,
    )

    uow = SqlAlchemyUnitOfWork(session_factory)
    async with uow:
        session = cast(AsyncSession, uow.session)
        await SqlAlchemyRunRepo(session).create(run)
        await SqlAlchemyTaskRepo(session).create(task_1)
        await SqlAlchemyTaskRepo(session).create(task_2)
        await SqlAlchemyRunPacketRepo(session, settings=settings).create(packet)


@pytest.mark.asyncio
async def test_runs_list_and_show_payload_local_client_has_run_packet_and_tasks() -> None:
    settings = ReflexorSettings(max_run_packet_bytes=50_000)
    async with _in_memory_session_factory() as session_factory:
        run_id = _uuid()
        await _seed_run(
            session_factory,
            settings=settings,
            run_id=run_id,
            created_at_ms=1_000,
        )

        client = _build_local_client(session_factory, settings=settings)

        listed = await client.list_runs(limit=10, offset=0, status=None, since_ms=None)
        assert listed["total"] == 1
        assert listed["items"][0]["run_id"] == run_id
        assert listed["items"][0]["event_type"] == "webhook"
        assert listed["items"][0]["event_source"] == "tests"

        payload = await _build_run_show_payload(client, run_id)
        assert isinstance(payload["run"], dict)
        assert payload["run"]["summary"]["run_id"] == run_id
        assert payload["run"]["run_packet"]["event"]["payload"]["authorization"] == "<redacted>"

        assert payload["task_status_counts"]["queued"] == 1
        assert payload["task_status_counts"]["succeeded"] == 1


@pytest.mark.asyncio
async def test_runs_show_payload_uses_api_client_calls() -> None:
    run_id = "00000000-0000-4000-8000-000000000001"
    seen: list[httpx.Request] = []

    run_detail = {
        "summary": {
            "run_id": run_id,
            "created_at_ms": 1_000,
            "started_at_ms": None,
            "completed_at_ms": None,
            "status": "created",
            "event_type": "webhook",
            "event_source": "tests",
            "tasks_total": 2,
            "tasks_pending": 0,
            "tasks_queued": 1,
            "tasks_running": 0,
            "tasks_succeeded": 1,
            "tasks_failed": 0,
            "tasks_canceled": 0,
            "approvals_total": 0,
            "approvals_pending": 0,
        },
        "run_packet": {
            "run_id": run_id,
            "event": {
                "event_id": "e1",
                "type": "webhook",
                "source": "tests",
                "received_at_ms": 1_000,
                "payload": {"authorization": "<redacted>", "seq": 1},
            },
            "tasks": [],
            "tool_results": [],
            "policy_decisions": [],
            "created_at_ms": 1_000,
        },
    }
    tasks_page = {
        "limit": 200,
        "offset": 0,
        "total": 2,
        "items": [
            {
                "task_id": "t1",
                "run_id": run_id,
                "name": "task-1",
                "status": "queued",
                "attempts": 0,
                "max_attempts": 3,
                "timeout_s": 60,
                "depends_on": [],
                "tool_call_id": None,
                "tool_name": None,
                "permission_scope": None,
                "idempotency_key": None,
                "tool_call_status": None,
            },
            {
                "task_id": "t2",
                "run_id": run_id,
                "name": "task-2",
                "status": "succeeded",
                "attempts": 0,
                "max_attempts": 3,
                "timeout_s": 60,
                "depends_on": [],
                "tool_call_id": None,
                "tool_name": None,
                "permission_scope": None,
                "idempotency_key": None,
                "tool_call_status": None,
            },
        ],
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == f"/base/v1/runs/{run_id}":
            return httpx.Response(200, json=run_detail)
        if request.url.path == "/base/v1/tasks":
            assert request.url.params["run_id"] == run_id
            assert request.url.params["limit"] == "200"
            assert request.url.params["offset"] == "0"
            return httpx.Response(200, json=tasks_page)
        return httpx.Response(404, json={"error": "not found"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = ApiClient(base_url="https://example.test/base", http=http)
        payload = await _build_run_show_payload(client, run_id)

    assert payload["run"]["summary"]["run_id"] == run_id
    assert payload["task_status_counts"] == {"queued": 1, "succeeded": 1}
    assert len(seen) == 2

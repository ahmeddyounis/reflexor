from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import create_async_engine as sa_create_async_engine

from reflexor.config import ReflexorSettings
from reflexor.domain.enums import ApprovalStatus, TaskStatus, ToolCallStatus
from reflexor.domain.models import Task, ToolCall
from reflexor.executor.approval_store import DbApprovalStore
from reflexor.executor.concurrency import ConcurrencyLimiter
from reflexor.executor.idempotency import IdempotencyLedger
from reflexor.executor.retries import RetryPolicy
from reflexor.executor.service import ExecutionDisposition, ExecutorRepoFactory, ExecutorService
from reflexor.infra.db.engine import AsyncSessionFactory, create_async_session_factory
from reflexor.infra.db.models import ApprovalRow, Base, TaskRow, ToolCallRow
from reflexor.infra.db.repos import (
    SqlAlchemyApprovalRepo,
    SqlAlchemyIdempotencyLedger,
    SqlAlchemyRunPacketRepo,
    SqlAlchemyRunRepo,
    SqlAlchemyTaskRepo,
    SqlAlchemyToolCallRepo,
)
from reflexor.infra.db.unit_of_work import SqlAlchemyUnitOfWork
from reflexor.infra.queue.in_memory_queue import InMemoryQueue
from reflexor.orchestrator.clock import Clock
from reflexor.orchestrator.queue import Lease, TaskEnvelope
from reflexor.security.policy.enforcement import PolicyEnforcedToolRunner
from reflexor.security.policy.gate import PolicyGate
from reflexor.security.policy.rules import ApprovalRequiredRule, ScopeEnabledRule
from reflexor.storage.ports import RunRecord
from reflexor.storage.uow import DatabaseSession
from reflexor.tools.registry import ToolRegistry
from reflexor.tools.runner import ToolRunner
from reflexor.tools.sdk import ToolContext, ToolManifest, ToolResult


def _uuid() -> str:
    return str(uuid.uuid4())


@dataclass(slots=True)
class _MutableClock(Clock):
    now: int = 1_000
    monotonic: int = 0

    def now_ms(self) -> int:
        return self.now

    def monotonic_ms(self) -> int:
        return self.monotonic

    async def sleep(self, seconds: float) -> None:  # pragma: no cover
        _ = seconds
        raise AssertionError("sleep should not be called in this test")


class _Args(BaseModel):
    text: str


class _FlakyTool:
    manifest = ToolManifest(
        name="tests.flaky",
        version="0.1.0",
        description="Tool that fails once then succeeds.",
        permission_scope="fs.read",
        idempotent=True,
        max_output_bytes=10_000,
    )
    ArgsModel = _Args

    def __init__(self) -> None:
        self.calls = 0

    async def run(self, args: _Args, ctx: ToolContext) -> ToolResult:
        _ = (args, ctx)
        self.calls += 1
        if self.calls == 1:
            return ToolResult(ok=False, error_code="TIMEOUT", error_message="timed out")
        return ToolResult(ok=True, data={"ok": True})


class _AlwaysTimeoutTool:
    manifest = ToolManifest(
        name="tests.timeout",
        version="0.1.0",
        description="Tool that always times out.",
        permission_scope="fs.read",
        idempotent=True,
        max_output_bytes=10_000,
    )
    ArgsModel = _Args

    def __init__(self) -> None:
        self.calls = 0

    async def run(self, args: _Args, ctx: ToolContext) -> ToolResult:
        _ = (args, ctx)
        self.calls += 1
        return ToolResult(ok=False, error_code="TIMEOUT", error_message="timed out")


@asynccontextmanager
async def _sqlite_file_session_factory(
    tmp_path: Path,
) -> AsyncIterator[AsyncSessionFactory]:
    db_path = tmp_path / "reflexor_executor_retry_test.db"
    database_url = f"sqlite+aiosqlite:///{db_path}"

    engine = sa_create_async_engine(database_url, connect_args={"check_same_thread": False})
    session_factory = create_async_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield session_factory
    finally:
        await engine.dispose()
        db_path.unlink(missing_ok=True)


def _policy_runner(
    *,
    registry: ToolRegistry,
    settings: ReflexorSettings,
    uow_factory: Callable[[], SqlAlchemyUnitOfWork],
) -> PolicyEnforcedToolRunner:
    runner = ToolRunner(registry=registry, settings=settings)
    gate = PolicyGate(rules=[ScopeEnabledRule(), ApprovalRequiredRule()], settings=settings)
    approvals = DbApprovalStore(
        uow_factory=uow_factory,
        approval_repo=lambda session: SqlAlchemyApprovalRepo(cast(AsyncSession, session)),
    )
    return PolicyEnforcedToolRunner(
        registry=registry, runner=runner, gate=gate, approvals=approvals
    )


def _executor_service(
    session_factory: AsyncSessionFactory,
    *,
    settings: ReflexorSettings,
    registry: ToolRegistry,
    queue: InMemoryQueue,
    clock: Clock,
) -> ExecutorService:
    def uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(session_factory)

    repos = ExecutorRepoFactory(
        task_repo=lambda session: SqlAlchemyTaskRepo(cast(AsyncSession, session)),
        tool_call_repo=lambda session: SqlAlchemyToolCallRepo(cast(AsyncSession, session)),
        approval_repo=lambda session: SqlAlchemyApprovalRepo(cast(AsyncSession, session)),
        run_packet_repo=lambda session: SqlAlchemyRunPacketRepo(
            cast(AsyncSession, session), settings=settings
        ),
    )

    def ledger_factory(session: DatabaseSession) -> IdempotencyLedger:
        return SqlAlchemyIdempotencyLedger(cast(AsyncSession, session), settings=settings)

    return ExecutorService(
        uow_factory=uow_factory,
        repos=repos,
        queue=queue,
        policy_runner=_policy_runner(registry=registry, settings=settings, uow_factory=uow_factory),
        tool_registry=registry,
        idempotency_ledger=ledger_factory,
        retry_policy=RetryPolicy(max_attempts=3, base_delay_s=1.0, max_delay_s=10.0, jitter=0.0),
        limiter=ConcurrencyLimiter(max_global=10),
        clock=clock,
    )


def _envelope(*, task_id: str, run_id: str) -> TaskEnvelope:
    return TaskEnvelope(
        envelope_id=_uuid(),
        task_id=task_id,
        run_id=run_id,
        attempt=0,
        created_at_ms=0,
        available_at_ms=0,
    )


@pytest.mark.asyncio
async def test_transient_failure_schedules_delayed_retry_and_eventual_success(
    tmp_path: Path,
) -> None:
    clock = _MutableClock(now=1_000)
    tool = _FlakyTool()
    registry = ToolRegistry()
    registry.register(tool)

    settings = ReflexorSettings(workspace_root=tmp_path, enabled_scopes=["fs.read"])
    queue = InMemoryQueue(now_ms=clock.now_ms, default_visibility_timeout_s=60.0)

    async with _sqlite_file_session_factory(tmp_path) as session_factory:
        run_id = _uuid()
        tool_call_id = _uuid()
        task_id = _uuid()

        run = RunRecord(
            run_id=run_id,
            parent_run_id=None,
            created_at_ms=0,
            started_at_ms=None,
            completed_at_ms=None,
        )
        tool_call = ToolCall(
            tool_call_id=tool_call_id,
            tool_name=tool.manifest.name,
            args={"text": "hello"},
            permission_scope="fs.read",
            idempotency_key="k-retry",
            status=ToolCallStatus.PENDING,
            created_at_ms=0,
        )
        task = Task(
            task_id=task_id,
            run_id=run_id,
            name="retry-me",
            status=TaskStatus.QUEUED,
            tool_call=tool_call,
            max_attempts=3,
            timeout_s=60,
            created_at_ms=0,
        )

        uow = SqlAlchemyUnitOfWork(session_factory)
        async with uow:
            session = cast(AsyncSession, uow.session)
            await SqlAlchemyRunRepo(session).create(run)
            await SqlAlchemyTaskRepo(session).create(task)

        service = _executor_service(
            session_factory,
            settings=settings,
            registry=registry,
            queue=queue,
            clock=clock,
        )

        await queue.enqueue(_envelope(task_id=task_id, run_id=run_id))

        lease1 = await queue.dequeue(wait_s=0.0)
        assert lease1 is not None

        report1 = await service.process_lease(cast(Lease, lease1))
        assert report1.disposition == ExecutionDisposition.FAILED_TRANSIENT
        assert report1.retry_after_s == pytest.approx(1.0)
        assert tool.calls == 1

        uow1 = SqlAlchemyUnitOfWork(session_factory)
        async with uow1:
            session = cast(AsyncSession, uow1.session)
            task_row = await session.get(TaskRow, task_id)
            assert task_row is not None
            assert task_row.status == TaskStatus.FAILED.value
            assert task_row.attempts == 1

            tool_call_row = await session.get(ToolCallRow, tool_call_id)
            assert tool_call_row is not None
            assert tool_call_row.status == ToolCallStatus.FAILED.value

        assert await queue.dequeue(wait_s=0.0) is None

        clock.now += int((report1.retry_after_s or 0) * 1000)
        lease2 = await queue.dequeue(wait_s=0.0)
        assert lease2 is not None

        report2 = await service.process_lease(cast(Lease, lease2))
        assert report2.disposition == ExecutionDisposition.SUCCEEDED
        assert tool.calls == 2

        assert await queue.dequeue(wait_s=0.0) is None

        uow2 = SqlAlchemyUnitOfWork(session_factory)
        async with uow2:
            session = cast(AsyncSession, uow2.session)
            task_row = await session.get(TaskRow, task_id)
            assert task_row is not None
            assert task_row.status == TaskStatus.SUCCEEDED.value
            assert task_row.attempts == 2

            tool_call_row = await session.get(ToolCallRow, tool_call_id)
            assert tool_call_row is not None
            assert tool_call_row.status == ToolCallStatus.SUCCEEDED.value


@pytest.mark.asyncio
async def test_attempts_exhausted_does_not_requeue_transient_failure(tmp_path: Path) -> None:
    clock = _MutableClock(now=1_000)
    tool = _AlwaysTimeoutTool()
    registry = ToolRegistry()
    registry.register(tool)

    settings = ReflexorSettings(workspace_root=tmp_path, enabled_scopes=["fs.read"])
    queue = InMemoryQueue(now_ms=clock.now_ms, default_visibility_timeout_s=60.0)

    async with _sqlite_file_session_factory(tmp_path) as session_factory:
        run_id = _uuid()
        tool_call_id = _uuid()
        task_id = _uuid()

        run = RunRecord(
            run_id=run_id,
            parent_run_id=None,
            created_at_ms=0,
            started_at_ms=None,
            completed_at_ms=None,
        )
        tool_call = ToolCall(
            tool_call_id=tool_call_id,
            tool_name=tool.manifest.name,
            args={"text": "hello"},
            permission_scope="fs.read",
            idempotency_key="k-exhausted",
            status=ToolCallStatus.PENDING,
            created_at_ms=0,
        )
        task = Task(
            task_id=task_id,
            run_id=run_id,
            name="no-retry",
            status=TaskStatus.QUEUED,
            tool_call=tool_call,
            max_attempts=1,
            timeout_s=60,
            created_at_ms=0,
        )

        uow = SqlAlchemyUnitOfWork(session_factory)
        async with uow:
            session = cast(AsyncSession, uow.session)
            await SqlAlchemyRunRepo(session).create(run)
            await SqlAlchemyTaskRepo(session).create(task)

        service = _executor_service(
            session_factory,
            settings=settings,
            registry=registry,
            queue=queue,
            clock=clock,
        )

        await queue.enqueue(_envelope(task_id=task_id, run_id=run_id))
        lease = await queue.dequeue(wait_s=0.0)
        assert lease is not None

        report = await service.process_lease(cast(Lease, lease))
        assert report.disposition == ExecutionDisposition.FAILED_TRANSIENT
        assert tool.calls == 1

        clock.now += 60_000
        assert await queue.dequeue(wait_s=0.0) is None


@pytest.mark.asyncio
async def test_approval_required_is_acked_and_not_requeued(tmp_path: Path) -> None:
    clock = _MutableClock(now=1_000)
    tool = _FlakyTool()
    registry = ToolRegistry()
    registry.register(tool)

    settings = ReflexorSettings(
        workspace_root=tmp_path,
        enabled_scopes=["fs.read"],
        approval_required_scopes=["fs.read"],
    )
    queue = InMemoryQueue(now_ms=clock.now_ms, default_visibility_timeout_s=60.0)

    async with _sqlite_file_session_factory(tmp_path) as session_factory:
        run_id = _uuid()
        tool_call_id = _uuid()
        task_id = _uuid()

        run = RunRecord(
            run_id=run_id,
            parent_run_id=None,
            created_at_ms=0,
            started_at_ms=None,
            completed_at_ms=None,
        )
        tool_call = ToolCall(
            tool_call_id=tool_call_id,
            tool_name=tool.manifest.name,
            args={"text": "hello"},
            permission_scope="fs.read",
            idempotency_key="k-approval",
            status=ToolCallStatus.PENDING,
            created_at_ms=0,
        )
        task = Task(
            task_id=task_id,
            run_id=run_id,
            name="needs-approval",
            status=TaskStatus.QUEUED,
            tool_call=tool_call,
            max_attempts=3,
            timeout_s=60,
            created_at_ms=0,
        )

        uow = SqlAlchemyUnitOfWork(session_factory)
        async with uow:
            session = cast(AsyncSession, uow.session)
            await SqlAlchemyRunRepo(session).create(run)
            await SqlAlchemyTaskRepo(session).create(task)

        service = _executor_service(
            session_factory,
            settings=settings,
            registry=registry,
            queue=queue,
            clock=clock,
        )

        await queue.enqueue(_envelope(task_id=task_id, run_id=run_id))
        lease = await queue.dequeue(wait_s=0.0)
        assert lease is not None

        report = await service.process_lease(cast(Lease, lease))
        assert report.disposition == ExecutionDisposition.WAITING_APPROVAL
        assert report.approval_status == ApprovalStatus.PENDING
        assert tool.calls == 0

        clock.now += 60_000
        assert await queue.dequeue(wait_s=0.0) is None

        uow2 = SqlAlchemyUnitOfWork(session_factory)
        async with uow2:
            session = cast(AsyncSession, uow2.session)
            task_row = await session.get(TaskRow, task_id)
            assert task_row is not None
            assert task_row.status == TaskStatus.WAITING_APPROVAL.value

            approval_id = report.approval_id
            assert approval_id is not None
            approval_row = await session.get(ApprovalRow, approval_id)
            assert approval_row is not None
            assert approval_row.status == ApprovalStatus.PENDING.value

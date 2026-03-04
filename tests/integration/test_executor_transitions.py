from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
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
from reflexor.domain.enums import ApprovalStatus, TaskStatus, ToolCallStatus
from reflexor.domain.models import Task, ToolCall
from reflexor.executor.approval_store import DbApprovalStore
from reflexor.executor.concurrency import ConcurrencyLimiter
from reflexor.executor.idempotency import IdempotencyLedger
from reflexor.executor.retries import RetryPolicy
from reflexor.executor.service import ExecutionDisposition, ExecutorRepoFactory, ExecutorService
from reflexor.infra.db.engine import AsyncSessionFactory, create_async_session_factory
from reflexor.infra.db.models import (
    ApprovalRow,
    Base,
    IdempotencyLedgerRow,
    RunRow,
    TaskRow,
    ToolCallRow,
)
from reflexor.infra.db.repos import (
    SqlAlchemyApprovalRepo,
    SqlAlchemyIdempotencyLedger,
    SqlAlchemyRunPacketRepo,
    SqlAlchemyRunRepo,
    SqlAlchemyTaskRepo,
    SqlAlchemyToolCallRepo,
)
from reflexor.infra.db.unit_of_work import SqlAlchemyUnitOfWork
from reflexor.orchestrator.clock import Clock
from reflexor.orchestrator.queue import Lease, Queue, TaskEnvelope
from reflexor.security.policy.enforcement import PolicyEnforcedToolRunner
from reflexor.security.policy.gate import PolicyGate
from reflexor.security.policy.rules import (
    ApprovalRequiredRule,
    ScopeEnabledRule,
    ScopeMatchesManifestRule,
)
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


class _AdvancingTool:
    manifest = ToolManifest(
        name="tests.advancing",
        version="0.1.0",
        description="Tool that advances the clock for executor timestamp tests.",
        permission_scope="fs.read",
        idempotent=True,
        max_output_bytes=10_000,
    )
    ArgsModel = _Args

    def __init__(self, *, clock: _MutableClock) -> None:
        self.clock = clock
        self.calls = 0

    async def run(self, args: _Args, ctx: ToolContext) -> ToolResult:
        _ = (args, ctx)
        self.calls += 1
        self.clock.now += 1
        return ToolResult(ok=True, data={"ok": True})


class _NoopQueue(Queue):
    async def enqueue(self, envelope: TaskEnvelope) -> None:  # pragma: no cover
        _ = envelope
        raise AssertionError("enqueue should not be called in this test")

    async def dequeue(
        self,
        timeout_s: float | None = None,
        *,
        wait_s: float | None = 0.0,
    ) -> Lease | None:  # pragma: no cover
        _ = (timeout_s, wait_s)
        raise NotImplementedError

    async def ack(self, lease: Lease) -> None:  # pragma: no cover
        _ = lease
        raise NotImplementedError

    async def nack(
        self, lease: Lease, delay_s: float | None = None, reason: str | None = None
    ) -> None:  # pragma: no cover
        _ = (lease, delay_s, reason)
        raise NotImplementedError

    async def aclose(self) -> None:  # pragma: no cover
        return None


@asynccontextmanager
async def _sqlite_file_session_factory(
    tmp_path: Path,
) -> AsyncIterator[tuple[AsyncSessionFactory, Path]]:
    db_path = tmp_path / "reflexor_executor_test.db"
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


def _policy_runner(
    *,
    registry: ToolRegistry,
    settings: ReflexorSettings,
    uow_factory: Callable[[], SqlAlchemyUnitOfWork],
) -> PolicyEnforcedToolRunner:
    runner = ToolRunner(registry=registry, settings=settings)
    gate = PolicyGate(
        rules=[
            ScopeMatchesManifestRule(),
            ScopeEnabledRule(),
            ApprovalRequiredRule(),
        ],
        settings=settings,
    )
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
        queue=_NoopQueue(),
        policy_runner=_policy_runner(registry=registry, settings=settings, uow_factory=uow_factory),
        tool_registry=registry,
        idempotency_ledger=ledger_factory,
        retry_policy=RetryPolicy(max_attempts=3, base_delay_s=1.0, max_delay_s=10.0, jitter=0.0),
        limiter=ConcurrencyLimiter(max_global=10),
        clock=clock,
    )


@pytest.mark.asyncio
async def test_executor_persists_succeeded_statuses_and_timestamps(tmp_path: Path) -> None:
    clock = _MutableClock(now=1_000)
    tool = _AdvancingTool(clock=clock)
    registry = ToolRegistry()
    registry.register(tool)

    settings = ReflexorSettings(workspace_root=tmp_path, enabled_scopes=["fs.read"])

    async with _sqlite_file_session_factory(tmp_path) as (session_factory, db_path):
        assert db_path.exists()

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
            idempotency_key="k1",
            status=ToolCallStatus.PENDING,
            created_at_ms=0,
        )
        task = Task(
            task_id=task_id,
            run_id=run_id,
            name="do",
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
            clock=clock,
        )

        report = await service.execute_task(task_id)
        assert report.disposition == ExecutionDisposition.SUCCEEDED
        assert tool.calls == 1

        uow2 = SqlAlchemyUnitOfWork(session_factory)
        async with uow2:
            session = cast(AsyncSession, uow2.session)

            assert await session.get(RunRow, run_id) is not None

            tool_call_row = await session.get(ToolCallRow, tool_call_id)
            assert tool_call_row is not None
            assert tool_call_row.status == ToolCallStatus.SUCCEEDED.value
            assert tool_call_row.started_at_ms == 1_000
            assert tool_call_row.completed_at_ms == 1_001

            task_row = await session.get(TaskRow, task_id)
            assert task_row is not None
            assert task_row.status == TaskStatus.SUCCEEDED.value
            assert task_row.attempts == 1
            assert task_row.started_at_ms == 1_000
            assert task_row.completed_at_ms == 1_001

            ledger_row = await session.get(IdempotencyLedgerRow, "k1")
            assert ledger_row is not None
            assert ledger_row.status == "succeeded"


@pytest.mark.asyncio
async def test_executor_denies_when_tool_call_scope_mismatched(tmp_path: Path) -> None:
    clock = _MutableClock(now=1_000)
    tool = _AdvancingTool(clock=clock)
    registry = ToolRegistry()
    registry.register(tool)

    # Enable the (tampered) scope so the denial comes from the manifest mismatch rule.
    settings = ReflexorSettings(workspace_root=tmp_path, enabled_scopes=["net.http"])

    async with _sqlite_file_session_factory(tmp_path) as (session_factory, _db_path):
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
            permission_scope="net.http",
            idempotency_key="k-scope-mismatch",
            status=ToolCallStatus.PENDING,
            created_at_ms=0,
        )
        task = Task(
            task_id=task_id,
            run_id=run_id,
            name="scope-mismatch",
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
            clock=clock,
        )

        report = await service.execute_task(task_id)
        assert report.disposition == ExecutionDisposition.DENIED
        assert report.decision is not None
        assert report.decision.reason_code == "scope_mismatch"
        assert tool.calls == 0

        uow2 = SqlAlchemyUnitOfWork(session_factory)
        async with uow2:
            session = cast(AsyncSession, uow2.session)

            tool_call_row = await session.get(ToolCallRow, tool_call_id)
            assert tool_call_row is not None
            assert tool_call_row.status == ToolCallStatus.DENIED.value
            assert tool_call_row.started_at_ms is None
            assert tool_call_row.completed_at_ms == 1_000

            task_row = await session.get(TaskRow, task_id)
            assert task_row is not None
            assert task_row.status == TaskStatus.CANCELED.value
            assert task_row.attempts == 0
            assert task_row.started_at_ms is None
            assert task_row.completed_at_ms == 1_000

            ledger_row = await session.get(IdempotencyLedgerRow, "k-scope-mismatch")
            assert ledger_row is None


@pytest.mark.asyncio
async def test_executor_persists_waiting_approval_without_start_timestamps(tmp_path: Path) -> None:
    clock = _MutableClock(now=1_000)
    tool = _AdvancingTool(clock=clock)
    registry = ToolRegistry()
    registry.register(tool)

    settings = ReflexorSettings(
        workspace_root=tmp_path,
        enabled_scopes=["fs.read"],
        approval_required_scopes=["fs.read"],
    )

    async with _sqlite_file_session_factory(tmp_path) as (session_factory, _db_path):
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
            idempotency_key="k2",
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
            clock=clock,
        )

        report = await service.execute_task(task_id)
        assert report.disposition == ExecutionDisposition.WAITING_APPROVAL
        assert report.approval_id is not None
        assert report.approval_status == ApprovalStatus.PENDING
        assert tool.calls == 0

        uow2 = SqlAlchemyUnitOfWork(session_factory)
        async with uow2:
            session = cast(AsyncSession, uow2.session)

            tool_call_row = await session.get(ToolCallRow, tool_call_id)
            assert tool_call_row is not None
            assert tool_call_row.status == ToolCallStatus.PENDING.value
            assert tool_call_row.started_at_ms is None
            assert tool_call_row.completed_at_ms is None

            task_row = await session.get(TaskRow, task_id)
            assert task_row is not None
            assert task_row.status == TaskStatus.WAITING_APPROVAL.value
            assert task_row.attempts == 0
            assert task_row.started_at_ms is None
            assert task_row.completed_at_ms is None

            approval_row = await session.get(ApprovalRow, report.approval_id)
            assert approval_row is not None
            assert approval_row.status == ApprovalStatus.PENDING.value


@pytest.mark.asyncio
async def test_executor_creates_single_approval_per_tool_call_id(tmp_path: Path) -> None:
    clock = _MutableClock(now=1_000)
    tool = _AdvancingTool(clock=clock)
    registry = ToolRegistry()
    registry.register(tool)

    settings = ReflexorSettings(
        workspace_root=tmp_path,
        enabled_scopes=["fs.read"],
        approval_required_scopes=["fs.read"],
    )

    async with _sqlite_file_session_factory(tmp_path) as (session_factory, _db_path):
        run_id = _uuid()
        tool_call_id = _uuid()
        task1_id = _uuid()
        task2_id = _uuid()

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
        task1 = Task(
            task_id=task1_id,
            run_id=run_id,
            name="needs-approval-1",
            status=TaskStatus.QUEUED,
            tool_call=tool_call,
            max_attempts=3,
            timeout_s=60,
            created_at_ms=0,
        )
        task2 = Task(
            task_id=task2_id,
            run_id=run_id,
            name="needs-approval-2",
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
            await SqlAlchemyTaskRepo(session).create(task1)
            await SqlAlchemyTaskRepo(session).create(task2)

        service = _executor_service(
            session_factory,
            settings=settings,
            registry=registry,
            clock=clock,
        )

        report1 = await service.execute_task(task1_id)
        assert report1.disposition == ExecutionDisposition.WAITING_APPROVAL
        assert report1.approval_id is not None
        assert tool.calls == 0

        report2 = await service.execute_task(task2_id)
        assert report2.disposition == ExecutionDisposition.WAITING_APPROVAL
        assert report2.approval_id == report1.approval_id
        assert tool.calls == 0

        uow2 = SqlAlchemyUnitOfWork(session_factory)
        async with uow2:
            session = cast(AsyncSession, uow2.session)
            stmt = select(ApprovalRow).where(ApprovalRow.tool_call_id == tool_call_id)
            result = await session.execute(stmt)
            approvals = result.scalars().all()
            assert len(approvals) == 1

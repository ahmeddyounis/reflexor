from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import create_async_engine as sa_create_async_engine

from reflexor.config import ReflexorSettings
from reflexor.domain.enums import ApprovalStatus, TaskStatus, ToolCallStatus
from reflexor.domain.models import Task, ToolCall
from reflexor.executor.approval_store import DbApprovalStore
from reflexor.executor.concurrency import ConcurrencyLimiter
from reflexor.executor.idempotency import IdempotencyLedger
from reflexor.executor.retries import RetryPolicy
from reflexor.executor.service import ExecutorRepoFactory, ExecutorService
from reflexor.infra.db.engine import AsyncSessionFactory, create_async_session_factory
from reflexor.infra.db.models import (
    ApprovalRow,
    Base,
    RunPacketRow,
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
from reflexor.infra.queue.in_memory_queue import InMemoryQueue
from reflexor.orchestrator.clock import Clock
from reflexor.orchestrator.queue import TaskEnvelope
from reflexor.orchestrator.queue.observer import (
    QueueAckObservation,
    QueueDequeueObservation,
    QueueEnqueueObservation,
    QueueNackObservation,
    QueueObserver,
    QueueRedeliverObservation,
)
from reflexor.security.policy.enforcement import PolicyEnforcedToolRunner
from reflexor.security.policy.gate import PolicyGate
from reflexor.security.policy.rules import ApprovalRequiredRule, ScopeEnabledRule
from reflexor.storage.ports import RunRecord
from reflexor.storage.uow import DatabaseSession
from reflexor.tools.mock_tool import MockTool
from reflexor.tools.registry import ToolRegistry
from reflexor.tools.runner import ToolRunner
from reflexor.worker.runner import WorkerRunner


def _uuid() -> str:
    return str(uuid.uuid4())


@dataclass(slots=True)
class _FixedClock(Clock):
    now: int = 1_000

    def now_ms(self) -> int:
        return self.now

    def monotonic_ms(self) -> int:
        return self.now

    async def sleep(self, seconds: float) -> None:  # pragma: no cover
        _ = seconds
        raise AssertionError("sleep should not be called in this test")


@dataclass(slots=True)
class _RecordingObserver(QueueObserver):
    acks: list[QueueAckObservation] = field(default_factory=list)
    nacks: list[QueueNackObservation] = field(default_factory=list)
    dequeues: list[QueueDequeueObservation] = field(default_factory=list)

    ack_event: asyncio.Event = field(default_factory=asyncio.Event)

    def on_enqueue(self, observation: QueueEnqueueObservation) -> None:
        _ = observation

    def on_dequeue(self, observation: QueueDequeueObservation) -> None:
        if observation.lease is None:
            return
        self.dequeues.append(observation)

    def on_ack(self, observation: QueueAckObservation) -> None:
        self.acks.append(observation)
        self.ack_event.set()

    def on_nack(self, observation: QueueNackObservation) -> None:
        self.nacks.append(observation)

    def on_redeliver(self, observation: QueueRedeliverObservation) -> None:
        _ = observation


@asynccontextmanager
async def _sqlite_file_session_factory(
    tmp_path: Path,
) -> AsyncIterator[tuple[AsyncSessionFactory, Path]]:
    db_path = tmp_path / "reflexor_worker_approval_block.db"
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
    uow_factory: callable[[], SqlAlchemyUnitOfWork],
) -> PolicyEnforcedToolRunner:
    runner = ToolRunner(registry=registry, settings=settings)
    gate = PolicyGate(rules=[ScopeEnabledRule(), ApprovalRequiredRule()], settings=settings)
    approvals = DbApprovalStore(
        uow_factory=uow_factory,
        approval_repo=lambda session: SqlAlchemyApprovalRepo(cast(AsyncSession, session)),
    )
    return PolicyEnforcedToolRunner(
        registry=registry,
        runner=runner,
        gate=gate,
        approvals=approvals,
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
        retry_policy=RetryPolicy(max_attempts=1, base_delay_s=1.0, max_delay_s=10.0, jitter=0.0),
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
async def test_worker_approval_required_blocks_execution_and_does_not_retry(
    tmp_path: Path,
) -> None:
    clock = _FixedClock(now=1_000)
    observer = _RecordingObserver()
    queue = InMemoryQueue(now_ms=clock.now_ms, default_visibility_timeout_s=60.0, observer=observer)

    tool = MockTool(
        tool_name="tests.worker_approval_block",
        permission_scope="fs.write",
        side_effects=True,
        now_ms=clock.now_ms,
    )
    registry = ToolRegistry()
    registry.register(tool)

    settings = ReflexorSettings(
        workspace_root=tmp_path,
        dry_run=False,
        enabled_scopes=["fs.write"],
        approval_required_scopes=["fs.write"],
    )

    async with _sqlite_file_session_factory(tmp_path) as (session_factory, db_path):
        assert db_path.exists()

        run_id = _uuid()
        tool_call_id = _uuid()
        task_id_1 = _uuid()
        task_id_2 = _uuid()

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
            permission_scope=tool.manifest.permission_scope,
            idempotency_key="k-approval-block",
            status=ToolCallStatus.PENDING,
            created_at_ms=0,
        )
        task_1 = Task(
            task_id=task_id_1,
            run_id=run_id,
            name="needs-approval-1",
            status=TaskStatus.QUEUED,
            tool_call=tool_call,
            max_attempts=3,
            timeout_s=60,
            created_at_ms=0,
        )
        task_2 = Task(
            task_id=task_id_2,
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
            await SqlAlchemyTaskRepo(session).create(task_1)
            await SqlAlchemyTaskRepo(session).create(task_2)

        executor = _executor_service(
            session_factory,
            settings=settings,
            registry=registry,
            queue=queue,
            clock=clock,
        )

        stop_event = asyncio.Event()
        runner = WorkerRunner(
            queue=queue,
            executor=executor,
            visibility_timeout_s=settings.executor_visibility_timeout_s,
            dequeue_wait_s=0.0,
            stop_event=stop_event,
            install_signal_handlers=False,
        )

        worker_task = asyncio.create_task(runner.run())
        await asyncio.sleep(0)

        await queue.enqueue(_envelope(task_id=task_id_1, run_id=run_id))
        await asyncio.wait_for(observer.ack_event.wait(), timeout=1.0)
        observer.ack_event.clear()

        await queue.enqueue(_envelope(task_id=task_id_2, run_id=run_id))
        await asyncio.wait_for(observer.ack_event.wait(), timeout=1.0)

        assert len(tool.invocations) == 0
        assert len(observer.nacks) == 0

        stop_event.set()
        await asyncio.wait_for(worker_task, timeout=1.0)

        uow2 = SqlAlchemyUnitOfWork(session_factory)
        async with uow2:
            session = cast(AsyncSession, uow2.session)

            task_row_1 = await session.get(TaskRow, task_id_1)
            task_row_2 = await session.get(TaskRow, task_id_2)
            assert task_row_1 is not None
            assert task_row_2 is not None
            assert task_row_1.status == TaskStatus.WAITING_APPROVAL.value
            assert task_row_2.status == TaskStatus.WAITING_APPROVAL.value

            tool_call_row = await session.get(ToolCallRow, tool_call_id)
            assert tool_call_row is not None
            assert tool_call_row.status == ToolCallStatus.PENDING.value
            assert tool_call_row.started_at_ms is None
            assert tool_call_row.completed_at_ms is None

            approvals_stmt = select(ApprovalRow).where(ApprovalRow.tool_call_id == tool_call_id)
            approvals_result = await session.execute(approvals_stmt)
            approvals = approvals_result.scalars().all()
            assert len(approvals) == 1
            approval_row = approvals[0]
            assert approval_row.status == ApprovalStatus.PENDING.value
            assert approval_row.payload_hash is not None
            assert approval_row.preview is not None
            assert approval_row.preview.strip()

            packet_row = await session.get(RunPacketRow, run_id)
            assert packet_row is not None
            tool_results = packet_row.packet.get("tool_results")
            assert isinstance(tool_results, list)
            assert len(tool_results) == 2

            approval_ids: set[str] = set()
            for item in tool_results:
                item_dict = cast(dict[str, object], item)
                approval_id = item_dict.get("approval_id")
                assert approval_id is not None
                approval_ids.add(cast(str, approval_id))
            assert approval_ids == {approval_row.approval_id}

            row_count_stmt = select(func.count()).select_from(ApprovalRow)
            row_count_result = await session.execute(row_count_stmt)
            assert row_count_result.scalar_one() == 1

    assert not db_path.exists()

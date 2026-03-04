from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import create_async_engine as sa_create_async_engine

from reflexor.config import ReflexorSettings
from reflexor.domain.enums import TaskStatus, ToolCallStatus
from reflexor.domain.models import Task, ToolCall
from reflexor.executor.approval_store import DbApprovalStore
from reflexor.executor.concurrency import ConcurrencyLimiter
from reflexor.executor.retries import RetryPolicy
from reflexor.executor.service import ExecutorRepoFactory, ExecutorService
from reflexor.infra.db.engine import AsyncSessionFactory, create_async_session_factory
from reflexor.infra.db.models import Base, IdempotencyLedgerRow, RunPacketRow, TaskRow, ToolCallRow
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
from reflexor.security.policy.rules import ScopeEnabledRule
from reflexor.storage.idempotency import IdempotencyLedger
from reflexor.storage.ports import RunRecord
from reflexor.storage.uow import DatabaseSession
from reflexor.tools.mock_tool import MockTool
from reflexor.tools.registry import ToolRegistry
from reflexor.tools.runner import ToolRunner
from reflexor.worker.runner import WorkerRunner


def _uuid() -> str:
    return str(uuid.uuid4())


@dataclass(slots=True)
class _MutableClock(Clock):
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
    nacks: list[QueueNackObservation] = field(default_factory=list)
    dequeues: list[QueueDequeueObservation] = field(default_factory=list)
    acks: list[QueueAckObservation] = field(default_factory=list)

    nack_event: asyncio.Event = field(default_factory=asyncio.Event)
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
        self.nack_event.set()

    def on_redeliver(self, observation: QueueRedeliverObservation) -> None:
        _ = observation


@asynccontextmanager
async def _sqlite_file_session_factory(
    tmp_path: Path,
) -> AsyncIterator[tuple[AsyncSessionFactory, Path]]:
    db_path = tmp_path / "reflexor_worker_retry_flow.db"
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
    gate = PolicyGate(rules=[ScopeEnabledRule()], settings=settings)
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
async def test_worker_transient_failure_delayed_retry_then_success(tmp_path: Path) -> None:
    clock = _MutableClock(now=1_000)
    observer = _RecordingObserver()
    queue = InMemoryQueue(now_ms=clock.now_ms, default_visibility_timeout_s=60.0, observer=observer)

    tool = MockTool(
        tool_name="tests.worker_retry",
        permission_scope="fs.read",
        now_ms=clock.now_ms,
    )
    raw_args = {"text": "hello"}
    tool.set_transient_failures_then_success(raw_args, failures=1, error_code="TOOL_ERROR")

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
            args=dict(raw_args),
            permission_scope=tool.manifest.permission_scope,
            idempotency_key="k-worker-retry",
            status=ToolCallStatus.PENDING,
            created_at_ms=0,
        )
        task = Task(
            task_id=task_id,
            run_id=run_id,
            name="worker-retry",
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

        await queue.enqueue(_envelope(task_id=task_id, run_id=run_id))

        await asyncio.wait_for(observer.nack_event.wait(), timeout=1.0)
        assert len(tool.invocations) == 1
        assert len(observer.dequeues) == 1
        assert len(observer.nacks) == 1

        nack = observer.nacks[0]
        assert nack.delay_s == pytest.approx(1.0)

        delay_ms = int(nack.delay_s * 1000)
        due_ms = nack.now_ms + delay_ms

        clock.now = due_ms - 1
        await asyncio.sleep(0)
        assert len(tool.invocations) == 1
        assert len(observer.dequeues) == 1

        clock.now = due_ms
        await asyncio.wait_for(observer.ack_event.wait(), timeout=1.0)
        assert len(tool.invocations) == 2
        assert len(observer.dequeues) == 2

        stop_event.set()
        await asyncio.wait_for(worker_task, timeout=1.0)

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

            ledger_row = await session.get(IdempotencyLedgerRow, "k-worker-retry")
            assert ledger_row is not None
            assert ledger_row.status == "succeeded"

            packet_row = await session.get(RunPacketRow, run_id)
            assert packet_row is not None
            tool_results = packet_row.packet.get("tool_results")
            assert isinstance(tool_results, list)
            assert len(tool_results) == 2

            statuses = [cast(dict[str, object], item).get("status") for item in tool_results]
            assert statuses == ["failed_transient", "succeeded"]

            dumped = json.dumps(packet_row.packet, ensure_ascii=False, separators=(",", ":"))
            assert run_id in dumped
            assert tool_call_id in dumped

    assert not db_path.exists()

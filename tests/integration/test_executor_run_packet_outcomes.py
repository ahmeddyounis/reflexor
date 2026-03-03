from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import create_async_engine as sa_create_async_engine

from reflexor.config import ReflexorSettings
from reflexor.domain.enums import TaskStatus, ToolCallStatus
from reflexor.domain.models import Task, ToolCall
from reflexor.executor.concurrency import ConcurrencyLimiter
from reflexor.executor.idempotency import IdempotencyLedger
from reflexor.executor.retries import RetryPolicy
from reflexor.executor.service import ExecutionDisposition, ExecutorRepoFactory, ExecutorService
from reflexor.infra.db.engine import AsyncSessionFactory, create_async_session_factory
from reflexor.infra.db.models import Base, RunPacketRow
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
from reflexor.security.policy.approvals import InMemoryApprovalStore
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
class _FixedClock(Clock):
    now: int = 1_000
    monotonic: int = 0

    def now_ms(self) -> int:
        return self.now

    def monotonic_ms(self) -> int:
        return self.monotonic

    async def sleep(self, seconds: float) -> None:  # pragma: no cover
        _ = seconds
        raise AssertionError("sleep should not be called in this test")


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


class _Args(BaseModel):
    text: str


class _SecretFlakyTool:
    manifest = ToolManifest(
        name="tests.secret_flaky",
        version="0.1.0",
        description="Tool for run packet execution outcome persistence tests.",
        permission_scope="fs.read",
        idempotent=True,
        max_output_bytes=10_000,
    )
    ArgsModel = _Args

    def __init__(self, *, secret: str) -> None:
        self._secret = secret
        self.calls = 0

    async def run(self, args: _Args, ctx: ToolContext) -> ToolResult:
        _ = (args, ctx)
        self.calls += 1
        payload = "x" * 5_000
        if self.calls == 1:
            return ToolResult(
                ok=False,
                error_code="TIMEOUT",
                error_message=f"Bearer {self._secret} timed out",
                debug={"authorization": f"Bearer {self._secret}", "output": payload},
            )
        return ToolResult(ok=True, data={"note": f"Bearer {self._secret}", "output": payload})


@asynccontextmanager
async def _sqlite_file_session_factory(
    tmp_path: Path,
) -> AsyncIterator[tuple[AsyncSessionFactory, Path]]:
    db_path = tmp_path / "reflexor_executor_run_packet_test.db"
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
    *, registry: ToolRegistry, settings: ReflexorSettings
) -> PolicyEnforcedToolRunner:
    runner = ToolRunner(registry=registry, settings=settings)
    gate = PolicyGate(rules=[ScopeEnabledRule(), ApprovalRequiredRule()], settings=settings)
    approvals = InMemoryApprovalStore()
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
        policy_runner=_policy_runner(registry=registry, settings=settings),
        tool_registry=registry,
        idempotency_ledger=ledger_factory,
        retry_policy=RetryPolicy(max_attempts=3, base_delay_s=1.0, max_delay_s=10.0, jitter=0.0),
        limiter=ConcurrencyLimiter(max_global=10),
        clock=clock,
    )


@pytest.mark.asyncio
async def test_executor_persists_sanitized_execution_attempts_in_run_packet(tmp_path: Path) -> None:
    secret = "SUPERSECRETTOKENVALUE"
    clock = _FixedClock(now=1_000)
    tool = _SecretFlakyTool(secret=secret)
    registry = ToolRegistry()
    registry.register(tool)

    settings = ReflexorSettings(
        workspace_root=tmp_path,
        enabled_scopes=["fs.read"],
        max_tool_output_bytes=200,
        max_run_packet_bytes=10_000,
    )

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
            args={"text": f"Bearer {secret}"},
            permission_scope="fs.read",
            idempotency_key="k-run-packet",
            status=ToolCallStatus.PENDING,
            created_at_ms=0,
        )
        task = Task(
            task_id=task_id,
            run_id=run_id,
            name="persist-run-packet-outcomes",
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

        report1 = await service.execute_task(task_id)
        assert report1.disposition == ExecutionDisposition.FAILED_TRANSIENT
        assert tool.calls == 1

        report2 = await service.execute_task(task_id)
        assert report2.disposition == ExecutionDisposition.SUCCEEDED
        assert tool.calls == 2

        uow2 = SqlAlchemyUnitOfWork(session_factory)
        async with uow2:
            session = cast(AsyncSession, uow2.session)
            row = await session.get(RunPacketRow, run_id)
            assert row is not None

            dumped = json.dumps(row.packet, ensure_ascii=False, separators=(",", ":"))
            assert secret not in dumped
            assert "<redacted>" in dumped
            assert "<truncated>" in dumped

            tool_results = row.packet.get("tool_results")
            assert isinstance(tool_results, list)
            assert len(tool_results) == 2

            first, second = tool_results
            for entry in (first, second):
                assert isinstance(entry, dict)
                assert entry.get("tool_call_id") == tool_call_id
                assert entry.get("status") in {
                    ExecutionDisposition.FAILED_TRANSIENT.value,
                    ExecutionDisposition.SUCCEEDED.value,
                }
                assert "error_code" in entry
                assert isinstance(entry.get("retry"), dict)
                assert isinstance(entry.get("policy_decision"), dict)
                assert "result_summary" in entry

            assert first["status"] == ExecutionDisposition.FAILED_TRANSIENT.value
            assert first.get("error_code") == "TIMEOUT"
            assert first["retry"]["will_retry"] is True

            assert second["status"] == ExecutionDisposition.SUCCEEDED.value
            assert second["retry"]["will_retry"] is False

    assert not db_path.exists()

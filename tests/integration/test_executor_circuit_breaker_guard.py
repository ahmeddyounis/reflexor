from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest
from prometheus_client import generate_latest
from prometheus_client.parser import text_string_to_metric_families
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
from reflexor.guards import GuardChain, PolicyGuard
from reflexor.guards.circuit_breaker import (
    CircuitBreakerGuard,
    CircuitBreakerSpec,
    InMemoryCircuitBreaker,
)
from reflexor.infra.db.engine import AsyncSessionFactory, create_async_session_factory
from reflexor.infra.db.models import Base
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
from reflexor.observability.metrics import ReflexorMetrics
from reflexor.orchestrator.clock import Clock
from reflexor.orchestrator.queue import Lease, TaskEnvelope
from reflexor.security.policy.approvals import InMemoryApprovalStore
from reflexor.security.policy.enforcement import PolicyEnforcedToolRunner
from reflexor.security.policy.gate import PolicyGate
from reflexor.security.policy.rules import NetworkAllowlistRule, ScopeEnabledRule
from reflexor.security.scopes import Scope
from reflexor.storage.ports import RunRecord
from reflexor.storage.uow import DatabaseSession
from reflexor.tools.registry import ToolRegistry
from reflexor.tools.runner import ToolRunner
from reflexor.tools.sdk import ToolContext, ToolManifest, ToolResult


def _uuid() -> str:
    return str(uuid.uuid4())


def _metric_value(
    text: str,
    *,
    name: str,
    labels: dict[str, str] | None = None,
) -> float | None:
    for family in text_string_to_metric_families(text):
        for sample in family.samples:
            if sample.name != name:
                continue
            if labels is not None and any(sample.labels.get(k) != v for k, v in labels.items()):
                continue
            return float(sample.value)
    return None


@dataclass(slots=True)
class _MutableClock(Clock):
    now: int = 0
    monotonic: int = 0

    def now_ms(self) -> int:
        return int(self.now)

    def monotonic_ms(self) -> int:
        return int(self.monotonic)

    async def sleep(self, seconds: float) -> None:  # pragma: no cover
        _ = seconds
        raise AssertionError("sleep should not be called in this test")


class _Args(BaseModel):
    url: str


class _FlakyBlockingTool:
    manifest = ToolManifest(
        name="tests.circuit_breaker",
        version="0.1.0",
        description="Tool that fails twice, then blocks once, then succeeds.",
        permission_scope=Scope.NET_HTTP.value,
        idempotent=False,
        max_output_bytes=10_000,
    )
    ArgsModel = _Args

    def __init__(self) -> None:
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def run(self, args: _Args, ctx: ToolContext) -> ToolResult:
        _ = (args, ctx)
        self.calls += 1
        if self.calls <= 2:
            return ToolResult(ok=False, error_code="TIMEOUT", error_message="timed out")
        if self.calls == 3:
            self.started.set()
            await self.release.wait()
        return ToolResult(ok=True, data={"ok": True})


@asynccontextmanager
async def _sqlite_file_session_factory(
    tmp_path: Path,
) -> AsyncIterator[AsyncSessionFactory]:
    db_path = tmp_path / "reflexor_executor_circuit_breaker_test.db"
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
    settings: ReflexorSettings,
    registry: ToolRegistry,
    breaker: InMemoryCircuitBreaker,
    metrics: ReflexorMetrics,
    now_ms: Callable[[], int],
) -> PolicyEnforcedToolRunner:
    runner = ToolRunner(registry=registry, settings=settings)
    gate = PolicyGate(rules=[ScopeEnabledRule(), NetworkAllowlistRule()], settings=settings)
    approvals = InMemoryApprovalStore()
    guard_chain = GuardChain(
        [
            PolicyGuard(gate=gate),
            CircuitBreakerGuard(breaker=breaker, metrics=metrics, half_open_throttle_delay_s=0.1),
        ]
    )
    return PolicyEnforcedToolRunner(
        registry=registry,
        runner=runner,
        gate=gate,
        approvals=approvals,
        guard_chain=guard_chain,
        metrics=metrics,
        now_ms=now_ms,
    )


def _envelope(*, task_id: str, run_id: str, available_at_ms: int) -> TaskEnvelope:
    return TaskEnvelope(
        envelope_id=_uuid(),
        task_id=task_id,
        run_id=run_id,
        attempt=0,
        created_at_ms=0,
        available_at_ms=int(available_at_ms),
    )


@pytest.mark.asyncio
async def test_circuit_breaker_opens_fast_denies_and_recovers(tmp_path: Path) -> None:
    clock = _MutableClock(now=0)
    metrics = ReflexorMetrics.build()
    tool = _FlakyBlockingTool()

    registry = ToolRegistry()
    registry.register(tool)

    settings = ReflexorSettings(
        workspace_root=tmp_path,
        enabled_scopes=[Scope.NET_HTTP.value],
        http_allowed_domains=["example.com"],
    )

    breaker = InMemoryCircuitBreaker(
        spec=CircuitBreakerSpec(
            failure_threshold=2,
            window_s=60.0,
            open_cooldown_s=5.0,
            half_open_max_calls=1,
            success_threshold=1,
        )
    )

    async with _sqlite_file_session_factory(tmp_path) as session_factory:

        def uow_factory() -> SqlAlchemyUnitOfWork:
            return SqlAlchemyUnitOfWork(session_factory)

        repos = ExecutorRepoFactory(
            task_repo=lambda session: SqlAlchemyTaskRepo(cast(AsyncSession, session)),
            tool_call_repo=lambda session: SqlAlchemyToolCallRepo(cast(AsyncSession, session)),
            approval_repo=lambda session: SqlAlchemyApprovalRepo(cast(AsyncSession, session)),
            run_packet_repo=lambda session: SqlAlchemyRunPacketRepo(
                cast(AsyncSession, session),
                settings=settings,
            ),
        )

        def ledger_factory(session: DatabaseSession) -> IdempotencyLedger:
            return SqlAlchemyIdempotencyLedger(cast(AsyncSession, session), settings=settings)

        queue = InMemoryQueue(now_ms=clock.now_ms, default_visibility_timeout_s=60.0)
        policy_runner = _policy_runner(
            settings=settings,
            registry=registry,
            breaker=breaker,
            metrics=metrics,
            now_ms=clock.now_ms,
        )

        service = ExecutorService(
            uow_factory=uow_factory,
            repos=repos,
            queue=queue,
            policy_runner=policy_runner,
            tool_registry=registry,
            idempotency_ledger=ledger_factory,
            retry_policy=RetryPolicy(
                max_attempts=3, base_delay_s=1.0, max_delay_s=10.0, jitter=0.0
            ),
            limiter=ConcurrencyLimiter(max_global=10),
            clock=clock,
            metrics=metrics,
            circuit_breaker=breaker,
        )

        run_id = _uuid()

        def make_task(*, tool_call_id: str, task_id: str, max_attempts: int) -> Task:
            tc = ToolCall(
                tool_call_id=tool_call_id,
                tool_name=tool.manifest.name,
                args={"url": "https://example.com/path"},
                permission_scope=tool.manifest.permission_scope,
                idempotency_key=f"k-{tool_call_id}",
                status=ToolCallStatus.PENDING,
                created_at_ms=0,
            )
            return Task(
                task_id=task_id,
                run_id=run_id,
                name="cb",
                status=TaskStatus.QUEUED,
                tool_call=tc,
                max_attempts=max_attempts,
                timeout_s=60,
                created_at_ms=0,
            )

        t1 = make_task(tool_call_id=_uuid(), task_id=_uuid(), max_attempts=1)
        t2 = make_task(tool_call_id=_uuid(), task_id=_uuid(), max_attempts=1)
        t3 = make_task(tool_call_id=_uuid(), task_id=_uuid(), max_attempts=3)
        t4 = make_task(tool_call_id=_uuid(), task_id=_uuid(), max_attempts=3)

        uow = uow_factory()
        async with uow:
            session = cast(AsyncSession, uow.session)
            await SqlAlchemyRunRepo(session).create(
                RunRecord(
                    run_id=run_id,
                    parent_run_id=None,
                    created_at_ms=0,
                    started_at_ms=None,
                    completed_at_ms=None,
                )
            )
            await SqlAlchemyTaskRepo(session).create(t1)
            await SqlAlchemyTaskRepo(session).create(t2)
            await SqlAlchemyTaskRepo(session).create(t3)
            await SqlAlchemyTaskRepo(session).create(t4)

        await queue.enqueue(_envelope(task_id=t1.task_id, run_id=run_id, available_at_ms=0))
        await queue.enqueue(_envelope(task_id=t2.task_id, run_id=run_id, available_at_ms=0))

        lease1 = await queue.dequeue(wait_s=0.0)
        assert lease1 is not None
        r1 = await service.process_lease(cast(Lease, lease1))
        assert r1.disposition == ExecutionDisposition.FAILED_TRANSIENT
        assert tool.calls == 1

        lease2 = await queue.dequeue(wait_s=0.0)
        assert lease2 is not None
        r2 = await service.process_lease(cast(Lease, lease2))
        assert r2.disposition == ExecutionDisposition.FAILED_TRANSIENT
        assert tool.calls == 2

        await queue.enqueue(_envelope(task_id=t3.task_id, run_id=run_id, available_at_ms=0))
        lease3 = await queue.dequeue(wait_s=0.0)
        assert lease3 is not None
        r3 = await service.process_lease(cast(Lease, lease3))
        assert r3.disposition == ExecutionDisposition.FAILED_TRANSIENT
        assert r3.result.error_code == "execution_delayed"
        assert r3.retry_after_s == pytest.approx(5.0)
        assert tool.calls == 2

        clock.now += 5_000
        await queue.enqueue(_envelope(task_id=t4.task_id, run_id=run_id, available_at_ms=clock.now))

        lease_a = await queue.dequeue(wait_s=0.0)
        assert lease_a is not None
        task_a = asyncio.create_task(service.process_lease(cast(Lease, lease_a)))
        await asyncio.wait_for(tool.started.wait(), timeout=1.0)

        lease_b = await queue.dequeue(wait_s=0.0)
        assert lease_b is not None
        report_b = await service.process_lease(cast(Lease, lease_b))
        assert report_b.result.error_code == "execution_delayed"
        assert tool.calls == 3

        tool.release.set()
        report_a = await asyncio.wait_for(task_a, timeout=1.0)
        assert report_a.disposition == ExecutionDisposition.SUCCEEDED

        clock.now += 200
        lease_b2 = await queue.dequeue(wait_s=0.0)
        assert lease_b2 is not None
        report_b2 = await service.process_lease(cast(Lease, lease_b2))
        assert report_b2.disposition == ExecutionDisposition.SUCCEEDED
        assert tool.calls == 4

        packet_uow = uow_factory()
        async with packet_uow:
            session = cast(AsyncSession, packet_uow.session)
            packet = await SqlAlchemyRunPacketRepo(session, settings=settings).get(run_id)
            assert packet is not None

            delayed = [
                entry
                for entry in packet.tool_results
                if entry.get("error_code") == "execution_delayed"
            ]
            assert any(
                entry.get("policy_decision", {}).get("reason_code") == "circuit_open"
                for entry in delayed
            )
            assert any(
                entry.get("policy_decision", {}).get("reason_code") == "circuit_half_open"
                for entry in delayed
            )
            assert all(entry.get("guard_decision", {}).get("action") == "delay" for entry in delayed)
            assert any(entry.get("guard_decision", {}).get("reason_code") == "circuit_open" for entry in delayed)
            assert any(
                entry.get("guard_decision", {}).get("reason_code") == "circuit_half_open"
                for entry in delayed
            )

        text = generate_latest(metrics.registry).decode()
        assert (
            _metric_value(
                text,
                name="circuit_breaker_checks_total",
                labels={"state": "open", "allowed": "false"},
            )
            == 1.0
        )
        assert (
            _metric_value(
                text,
                name="circuit_breaker_checks_total",
                labels={"state": "half_open", "allowed": "true"},
            )
            == 1.0
        )
        assert (
            _metric_value(
                text,
                name="circuit_breaker_checks_total",
                labels={"state": "half_open", "allowed": "false"},
            )
            == 1.0
        )
        assert (
            _metric_value(
                text,
                name="circuit_open_total",
                labels={"tool_name": "tests.circuit_breaker", "destination": "example.com"},
            )
            == 1.0
        )
        assert (
            _metric_value(
                text,
                name="retry_after_seconds_count",
                labels={"reason_code": "circuit_open", "tool_name": "tests.circuit_breaker"},
            )
            == 1.0
        )

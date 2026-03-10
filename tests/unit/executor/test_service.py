from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest
from prometheus_client import generate_latest
from prometheus_client.parser import text_string_to_metric_families
from pydantic import BaseModel

from reflexor.config import ReflexorSettings
from reflexor.domain.enums import ApprovalStatus, TaskStatus, ToolCallStatus
from reflexor.domain.models import Approval, Task, ToolCall
from reflexor.domain.models_event import Event
from reflexor.domain.models_run_packet import RunPacket
from reflexor.executor.concurrency import ConcurrencyLimiter
from reflexor.executor.retries import RetryPolicy
from reflexor.executor.service import (
    ExecutionDisposition,
    ExecutorRepoFactory,
    ExecutorService,
)
from reflexor.observability.metrics import ReflexorMetrics
from reflexor.orchestrator.clock import Clock
from reflexor.orchestrator.queue import Lease, Queue, TaskEnvelope
from reflexor.security.policy.approvals import InMemoryApprovalStore
from reflexor.security.policy.enforcement import PolicyEnforcedToolRunner
from reflexor.security.policy.gate import PolicyGate
from reflexor.security.policy.rules import ApprovalRequiredRule, ScopeEnabledRule
from reflexor.storage.idempotency import (
    CachedOutcome,
    IdempotencyLedger,
    LedgerStatus,
    OutcomeToCache,
)
from reflexor.storage.uow import DatabaseSession, UnitOfWork
from reflexor.tools.mock_tool import MockTool
from reflexor.tools.registry import ToolRegistry
from reflexor.tools.runner import ToolRunner
from reflexor.tools.sdk import ToolContext, ToolManifest, ToolResult


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
class _FixedClock(Clock):
    now: int = 123
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
        raise AssertionError("enqueue should not be called in these tests")

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


class _RecordingQueue(Queue):
    def __init__(self) -> None:
        self.enqueued: list[TaskEnvelope] = []

    async def enqueue(self, envelope: TaskEnvelope) -> None:
        self.enqueued.append(envelope)

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


class _NullUnitOfWork(UnitOfWork):
    def __init__(self, session: object) -> None:
        self._session = session

    @property
    def session(self) -> DatabaseSession:
        return self._session  # type: ignore[return-value]

    async def __aenter__(self) -> _NullUnitOfWork:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object | None,
    ) -> None:
        _ = (exc_type, exc, tb)
        return None


class _InMemoryTaskRepo:
    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}

    async def create(self, task: Task) -> Task:
        self._tasks[task.task_id] = task
        return task

    async def get(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    async def update_status(self, task_id: str, status: TaskStatus) -> Task:
        task = self._tasks[task_id]
        updated = task.model_copy(update={"status": status})
        self._tasks[task_id] = updated
        return updated

    async def update(self, task: Task) -> Task:
        self._tasks[task.task_id] = task
        return task

    async def list_by_run(self, run_id: str) -> list[Task]:
        return [task for task in self._tasks.values() if task.run_id == run_id]


class _InMemoryToolCallRepo:
    def __init__(self) -> None:
        self._tool_calls: dict[str, ToolCall] = {}

    async def create(self, tool_call: ToolCall) -> ToolCall:
        self._tool_calls[tool_call.tool_call_id] = tool_call
        return tool_call

    async def get(self, tool_call_id: str) -> ToolCall | None:
        return self._tool_calls.get(tool_call_id)

    async def update_status(self, tool_call_id: str, status: ToolCallStatus) -> ToolCall:
        tool_call = self._tool_calls[tool_call_id]
        updated = tool_call.model_copy(update={"status": status})
        self._tool_calls[tool_call_id] = updated
        return updated

    async def update(self, tool_call: ToolCall) -> ToolCall:
        self._tool_calls[tool_call.tool_call_id] = tool_call
        return tool_call


class _InMemoryApprovalRepo:
    def __init__(self) -> None:
        self._approvals: dict[str, Approval] = {}
        self._by_tool_call: dict[str, str] = {}

    async def create(self, approval: Approval) -> Approval:
        self._approvals[approval.approval_id] = approval
        self._by_tool_call[approval.tool_call_id] = approval.approval_id
        return approval

    async def get(self, approval_id: str) -> Approval | None:
        return self._approvals.get(approval_id)

    async def get_by_tool_call(self, tool_call_id: str) -> Approval | None:
        approval_id = self._by_tool_call.get(tool_call_id)
        if approval_id is None:
            return None
        return self._approvals.get(approval_id)

    async def update_status(
        self,
        approval_id: str,
        status: ApprovalStatus,
        *,
        decided_at_ms: int | None = None,
        decided_by: str | None = None,
    ) -> Approval:
        approval = self._approvals[approval_id]
        updated = approval.model_copy(
            update={
                "status": status,
                "decided_at_ms": decided_at_ms,
                "decided_by": decided_by,
            }
        )
        self._approvals[approval_id] = updated
        self._by_tool_call[updated.tool_call_id] = updated.approval_id
        return updated


class _InMemoryRunPacketRepo:
    def __init__(self) -> None:
        self._packets: dict[str, RunPacket] = {}

    async def create(self, packet: RunPacket) -> RunPacket:
        self._packets[packet.run_id] = packet
        return packet

    async def get(self, run_id: str) -> RunPacket | None:
        return self._packets.get(run_id)


class _FakeLedger(IdempotencyLedger):
    def __init__(self) -> None:
        self._success: dict[str, CachedOutcome] = {}
        self.recorded_success: list[str] = []
        self.recorded_failure: list[tuple[str, bool]] = []

    def seed_success(self, key: str, outcome: CachedOutcome) -> None:
        self._success[key] = outcome

    async def get_success(self, key: str) -> CachedOutcome | None:
        return self._success.get(key)

    async def record_success(self, key: str, outcome: OutcomeToCache) -> None:
        _ = outcome
        self.recorded_success.append(key)

    async def record_failure(self, key: str, outcome: OutcomeToCache, transient: bool) -> None:
        _ = outcome
        self.recorded_failure.append((key, transient))


class _Args(BaseModel):
    text: str


class _RecordingTool:
    def __init__(self, *, result: ToolResult, idempotent: bool = True) -> None:
        self.calls: list[dict[str, object]] = []
        self.manifest = ToolManifest(
            name="tests.recording",
            version="0.1.0",
            description="Recording tool for executor tests.",
            permission_scope="fs.read",
            idempotent=idempotent,
            max_output_bytes=10_000,
        )
        self.ArgsModel = _Args
        self._result = result

    async def run(self, args: _Args, ctx: ToolContext) -> ToolResult:
        self.calls.append({"args": args.model_dump(mode="json"), "dry_run": ctx.dry_run})
        return self._result


def _policy_runner(
    *, settings: ReflexorSettings, registry: ToolRegistry, metrics: ReflexorMetrics | None = None
) -> PolicyEnforcedToolRunner:
    runner = ToolRunner(registry=registry, settings=settings)
    gate = PolicyGate(
        rules=[ScopeEnabledRule(), ApprovalRequiredRule()],
        settings=settings,
        metrics=metrics,
    )
    approvals = InMemoryApprovalStore()
    return PolicyEnforcedToolRunner(
        registry=registry,
        runner=runner,
        gate=gate,
        approvals=approvals,
        metrics=metrics,
    )


def _packet(*, run_id: str, task: Task) -> RunPacket:
    event = Event(
        event_id=str(uuid4()),
        type="evt",
        source="tests",
        received_at_ms=0,
        payload={},
    )
    return RunPacket(run_id=run_id, event=event, tasks=[task], created_at_ms=0)


async def _build_service(
    *,
    tmp_path: Path,
    settings: ReflexorSettings,
    tool: object,
    task: Task,
    task_repo: _InMemoryTaskRepo,
    tool_call_repo: _InMemoryToolCallRepo,
    approval_repo: _InMemoryApprovalRepo,
    packet_repo: _InMemoryRunPacketRepo,
    ledger: _FakeLedger,
    metrics: ReflexorMetrics | None = None,
    queue: Queue | None = None,
) -> ExecutorService:
    registry = ToolRegistry()
    registry.register(tool)  # type: ignore[arg-type]
    runner = _policy_runner(settings=settings, registry=registry, metrics=metrics)

    await task_repo.create(task)
    assert task.tool_call is not None
    await tool_call_repo.create(task.tool_call)
    await packet_repo.create(_packet(run_id=task.run_id, task=task))

    repos = ExecutorRepoFactory(
        task_repo=lambda _session: task_repo,
        tool_call_repo=lambda _session: tool_call_repo,
        approval_repo=lambda _session: approval_repo,
        run_packet_repo=lambda _session: packet_repo,
    )

    session_obj: object = object()

    def uow_factory() -> UnitOfWork:
        return _NullUnitOfWork(session_obj)

    def ledger_factory(_session: DatabaseSession) -> IdempotencyLedger:
        return ledger

    return ExecutorService(
        uow_factory=uow_factory,
        repos=repos,
        queue=_NoopQueue() if queue is None else queue,
        policy_runner=runner,
        tool_registry=registry,
        idempotency_ledger=ledger_factory,
        retry_policy=RetryPolicy(
            max_attempts=3,
            base_delay_s=1.0,
            max_delay_s=10.0,
            jitter=0.0,
        ),
        limiter=ConcurrencyLimiter(max_global=10),
        clock=_FixedClock(now=1_000),
        metrics=metrics,
    )


@pytest.mark.asyncio
async def test_execute_task_uses_cached_success_and_does_not_invoke_tool(tmp_path: Path) -> None:
    result = ToolResult(ok=True, data={"ok": True})
    tool = _RecordingTool(result=result, idempotent=True)

    run_id = str(uuid4())
    tool_call = ToolCall(
        tool_call_id=str(uuid4()),
        tool_name=tool.manifest.name,
        args={"text": "hello"},
        permission_scope=tool.manifest.permission_scope,
        idempotency_key="k-cache",
        status=ToolCallStatus.PENDING,
        created_at_ms=0,
    )
    task = Task(
        task_id=str(uuid4()),
        run_id=run_id,
        name="cached",
        status=TaskStatus.QUEUED,
        tool_call=tool_call,
        created_at_ms=0,
    )

    ledger = _FakeLedger()
    ledger.seed_success(
        "k-cache",
        CachedOutcome(
            idempotency_key="k-cache",
            tool_name=tool.manifest.name,
            status=LedgerStatus.SUCCEEDED,
            result=result,
            created_at_ms=0,
            updated_at_ms=0,
        ),
    )

    service = await _build_service(
        tmp_path=tmp_path,
        settings=ReflexorSettings(
            workspace_root=tmp_path,
            enabled_scopes=["fs.read"],
        ),
        tool=tool,
        task=task,
        task_repo=_InMemoryTaskRepo(),
        tool_call_repo=_InMemoryToolCallRepo(),
        approval_repo=_InMemoryApprovalRepo(),
        packet_repo=_InMemoryRunPacketRepo(),
        ledger=ledger,
    )

    report = await service.execute_task(task.task_id)

    assert report.disposition == ExecutionDisposition.CACHED
    assert report.used_cached_result is True
    assert tool.calls == []
    assert ledger.recorded_success == []


@pytest.mark.asyncio
async def test_execute_task_denied_updates_status_and_does_not_invoke_tool(tmp_path: Path) -> None:
    tool = _RecordingTool(result=ToolResult(ok=True, data={"ok": True}), idempotent=True)

    run_id = str(uuid4())
    tool_call = ToolCall(
        tool_call_id=str(uuid4()),
        tool_name=tool.manifest.name,
        args={"text": "hello"},
        permission_scope=tool.manifest.permission_scope,
        idempotency_key="k",
        status=ToolCallStatus.PENDING,
        created_at_ms=0,
    )
    task = Task(
        task_id=str(uuid4()),
        run_id=run_id,
        name="denied",
        status=TaskStatus.QUEUED,
        tool_call=tool_call,
        created_at_ms=0,
    )

    task_repo = _InMemoryTaskRepo()
    tool_call_repo = _InMemoryToolCallRepo()

    service = await _build_service(
        tmp_path=tmp_path,
        settings=ReflexorSettings(workspace_root=tmp_path, enabled_scopes=[]),
        tool=tool,
        task=task,
        task_repo=task_repo,
        tool_call_repo=tool_call_repo,
        approval_repo=_InMemoryApprovalRepo(),
        packet_repo=_InMemoryRunPacketRepo(),
        ledger=_FakeLedger(),
    )

    report = await service.execute_task(task.task_id)

    assert report.disposition == ExecutionDisposition.DENIED
    assert tool.calls == []
    assert (await task_repo.get(task.task_id)).status == TaskStatus.CANCELED  # type: ignore[union-attr]
    assert (await tool_call_repo.get(tool_call.tool_call_id)).status == ToolCallStatus.DENIED  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_execute_task_approval_required_sets_waiting_and_persists_approval(
    tmp_path: Path,
) -> None:
    tool = _RecordingTool(result=ToolResult(ok=True, data={"ok": True}), idempotent=True)

    run_id = str(uuid4())
    tool_call = ToolCall(
        tool_call_id=str(uuid4()),
        tool_name=tool.manifest.name,
        args={"text": "hello"},
        permission_scope=tool.manifest.permission_scope,
        idempotency_key="k",
        status=ToolCallStatus.PENDING,
        created_at_ms=0,
    )
    task = Task(
        task_id=str(uuid4()),
        run_id=run_id,
        name="needs-approval",
        status=TaskStatus.QUEUED,
        tool_call=tool_call,
        created_at_ms=0,
    )

    approval_repo = _InMemoryApprovalRepo()
    task_repo = _InMemoryTaskRepo()
    tool_call_repo = _InMemoryToolCallRepo()

    service = await _build_service(
        tmp_path=tmp_path,
        settings=ReflexorSettings(
            workspace_root=tmp_path,
            enabled_scopes=["fs.read"],
            approval_required_scopes=["fs.read"],
        ),
        tool=tool,
        task=task,
        task_repo=task_repo,
        tool_call_repo=tool_call_repo,
        approval_repo=approval_repo,
        packet_repo=_InMemoryRunPacketRepo(),
        ledger=_FakeLedger(),
    )

    report = await service.execute_task(task.task_id)

    assert report.disposition == ExecutionDisposition.WAITING_APPROVAL
    assert report.approval_id is not None
    assert report.approval_status == ApprovalStatus.PENDING
    assert tool.calls == []

    stored = await approval_repo.get_by_tool_call(tool_call.tool_call_id)
    assert stored is not None
    assert stored.approval_id == report.approval_id
    assert stored.run_id == run_id
    assert stored.task_id == task.task_id

    updated_task = await task_repo.get(task.task_id)
    assert updated_task is not None
    assert updated_task.status == TaskStatus.WAITING_APPROVAL


@pytest.mark.asyncio
async def test_execute_task_transient_failure_marks_failed_and_records_retry_after(
    tmp_path: Path,
) -> None:
    tool = _RecordingTool(
        result=ToolResult(ok=False, error_code="TIMEOUT", error_message="timed out"),
        idempotent=True,
    )

    run_id = str(uuid4())
    tool_call = ToolCall(
        tool_call_id=str(uuid4()),
        tool_name=tool.manifest.name,
        args={"text": "hello"},
        permission_scope=tool.manifest.permission_scope,
        idempotency_key="k",
        status=ToolCallStatus.PENDING,
        created_at_ms=0,
    )
    task = Task(
        task_id=str(uuid4()),
        run_id=run_id,
        name="timeout",
        status=TaskStatus.QUEUED,
        tool_call=tool_call,
        created_at_ms=0,
    )

    ledger = _FakeLedger()
    task_repo = _InMemoryTaskRepo()
    tool_call_repo = _InMemoryToolCallRepo()

    service = await _build_service(
        tmp_path=tmp_path,
        settings=ReflexorSettings(workspace_root=tmp_path, enabled_scopes=["fs.read"]),
        tool=tool,
        task=task,
        task_repo=task_repo,
        tool_call_repo=tool_call_repo,
        approval_repo=_InMemoryApprovalRepo(),
        packet_repo=_InMemoryRunPacketRepo(),
        ledger=ledger,
    )

    report = await service.execute_task(task.task_id)

    assert report.disposition == ExecutionDisposition.FAILED_TRANSIENT
    assert report.retry_after_s == pytest.approx(1.0)
    assert len(tool.calls) == 1
    assert (await task_repo.get(task.task_id)).status == TaskStatus.FAILED  # type: ignore[union-attr]
    assert (await tool_call_repo.get(tool_call.tool_call_id)).status == ToolCallStatus.FAILED  # type: ignore[union-attr]
    assert ledger.recorded_failure == [("k", True)]


@pytest.mark.asyncio
async def test_execute_task_permanent_failure_marks_failed_and_records_ledger_failure(
    tmp_path: Path,
) -> None:
    tool = _RecordingTool(
        result=ToolResult(ok=False, error_code="INVALID_ARGS", error_message="bad args"),
        idempotent=True,
    )

    run_id = str(uuid4())
    tool_call = ToolCall(
        tool_call_id=str(uuid4()),
        tool_name=tool.manifest.name,
        args={"text": "hello"},
        permission_scope=tool.manifest.permission_scope,
        idempotency_key="k",
        status=ToolCallStatus.PENDING,
        created_at_ms=0,
    )
    task = Task(
        task_id=str(uuid4()),
        run_id=run_id,
        name="invalid",
        status=TaskStatus.QUEUED,
        tool_call=tool_call,
        created_at_ms=0,
    )

    ledger = _FakeLedger()

    service = await _build_service(
        tmp_path=tmp_path,
        settings=ReflexorSettings(workspace_root=tmp_path, enabled_scopes=["fs.read"]),
        tool=tool,
        task=task,
        task_repo=_InMemoryTaskRepo(),
        tool_call_repo=_InMemoryToolCallRepo(),
        approval_repo=_InMemoryApprovalRepo(),
        packet_repo=_InMemoryRunPacketRepo(),
        ledger=ledger,
    )

    report = await service.execute_task(task.task_id)

    assert report.disposition == ExecutionDisposition.FAILED_PERMANENT
    assert report.retry_after_s is None
    assert ledger.recorded_failure == [("k", False)]


@pytest.mark.asyncio
async def test_execute_task_success_marks_succeeded_and_records_ledger_success(
    tmp_path: Path,
) -> None:
    tool = _RecordingTool(result=ToolResult(ok=True, data={"ok": True}), idempotent=True)

    run_id = str(uuid4())
    tool_call = ToolCall(
        tool_call_id=str(uuid4()),
        tool_name=tool.manifest.name,
        args={"text": "hello"},
        permission_scope=tool.manifest.permission_scope,
        idempotency_key="k",
        status=ToolCallStatus.PENDING,
        created_at_ms=0,
    )
    task = Task(
        task_id=str(uuid4()),
        run_id=run_id,
        name="success",
        status=TaskStatus.QUEUED,
        tool_call=tool_call,
        created_at_ms=0,
    )

    ledger = _FakeLedger()

    service = await _build_service(
        tmp_path=tmp_path,
        settings=ReflexorSettings(workspace_root=tmp_path, enabled_scopes=["fs.read"]),
        tool=tool,
        task=task,
        task_repo=_InMemoryTaskRepo(),
        tool_call_repo=_InMemoryToolCallRepo(),
        approval_repo=_InMemoryApprovalRepo(),
        packet_repo=_InMemoryRunPacketRepo(),
        ledger=ledger,
    )

    report = await service.execute_task(task.task_id)

    assert report.disposition == ExecutionDisposition.SUCCEEDED
    assert ledger.recorded_success == ["k"]
    assert len(tool.calls) == 1


@pytest.mark.asyncio
async def test_executor_metrics_cache_hit_increments_counters(tmp_path: Path) -> None:
    metrics = ReflexorMetrics.build()

    tool = MockTool(tool_name="tests.mock", permission_scope="fs.read", idempotent=True)
    run_id = str(uuid4())
    tool_call = ToolCall(
        tool_call_id=str(uuid4()),
        tool_name=tool.manifest.name,
        args={"text": "hello"},
        permission_scope=tool.manifest.permission_scope,
        idempotency_key="k-cache",
        status=ToolCallStatus.PENDING,
        created_at_ms=0,
    )
    task = Task(
        task_id=str(uuid4()),
        run_id=run_id,
        name="cached",
        status=TaskStatus.QUEUED,
        tool_call=tool_call,
        max_attempts=3,
        created_at_ms=0,
    )

    ledger = _FakeLedger()
    ledger.seed_success(
        "k-cache",
        CachedOutcome(
            idempotency_key="k-cache",
            tool_name=tool.manifest.name,
            status=LedgerStatus.SUCCEEDED,
            result=ToolResult(ok=True, data={"ok": True}),
            created_at_ms=0,
            updated_at_ms=0,
        ),
    )

    service = await _build_service(
        tmp_path=tmp_path,
        settings=ReflexorSettings(workspace_root=tmp_path, enabled_scopes=["fs.read"]),
        tool=tool,
        task=task,
        task_repo=_InMemoryTaskRepo(),
        tool_call_repo=_InMemoryToolCallRepo(),
        approval_repo=_InMemoryApprovalRepo(),
        packet_repo=_InMemoryRunPacketRepo(),
        ledger=ledger,
        metrics=metrics,
    )

    report = await service.execute_task(task.task_id)
    assert report.disposition == ExecutionDisposition.CACHED

    text = generate_latest(metrics.registry).decode()
    assert _metric_value(text, name="idempotency_cache_hits_total") == 1.0
    assert _metric_value(text, name="tasks_completed_total", labels={"status": "succeeded"}) == 1.0


@pytest.mark.asyncio
async def test_executor_metrics_success_increments_latency_and_outcomes(tmp_path: Path) -> None:
    metrics = ReflexorMetrics.build()

    tool = MockTool(tool_name="tests.mock", permission_scope="fs.read", idempotent=True)
    run_id = str(uuid4())
    tool_call = ToolCall(
        tool_call_id=str(uuid4()),
        tool_name=tool.manifest.name,
        args={"text": "hello"},
        permission_scope=tool.manifest.permission_scope,
        idempotency_key="k",
        status=ToolCallStatus.PENDING,
        created_at_ms=0,
    )
    task = Task(
        task_id=str(uuid4()),
        run_id=run_id,
        name="ok",
        status=TaskStatus.QUEUED,
        tool_call=tool_call,
        max_attempts=3,
        created_at_ms=0,
    )

    service = await _build_service(
        tmp_path=tmp_path,
        settings=ReflexorSettings(workspace_root=tmp_path, enabled_scopes=["fs.read"]),
        tool=tool,
        task=task,
        task_repo=_InMemoryTaskRepo(),
        tool_call_repo=_InMemoryToolCallRepo(),
        approval_repo=_InMemoryApprovalRepo(),
        packet_repo=_InMemoryRunPacketRepo(),
        ledger=_FakeLedger(),
        metrics=metrics,
    )

    report = await service.execute_task(task.task_id)
    assert report.disposition == ExecutionDisposition.SUCCEEDED

    text = generate_latest(metrics.registry).decode()
    assert (
        _metric_value(
            text,
            name="tool_latency_seconds_count",
            labels={"tool_name": tool.manifest.name, "ok": "true"},
        )
        == 1.0
    )
    assert _metric_value(text, name="tasks_completed_total", labels={"status": "succeeded"}) == 1.0
    assert (
        _metric_value(
            text,
            name="policy_decisions_total",
            labels={"action": "allow", "reason_code": "ok"},
        )
        == 1.0
    )


@pytest.mark.asyncio
async def test_executor_metrics_transient_failure_increments_retry_counter(tmp_path: Path) -> None:
    metrics = ReflexorMetrics.build()

    tool = MockTool(tool_name="tests.mock", permission_scope="fs.read", idempotent=True)
    tool.set_static_result(
        {"text": "hello"},
        ToolResult(ok=False, error_code="TOOL_ERROR", error_message="boom"),
    )

    run_id = str(uuid4())
    tool_call = ToolCall(
        tool_call_id=str(uuid4()),
        tool_name=tool.manifest.name,
        args={"text": "hello"},
        permission_scope=tool.manifest.permission_scope,
        idempotency_key="k",
        status=ToolCallStatus.PENDING,
        created_at_ms=0,
    )
    task = Task(
        task_id=str(uuid4()),
        run_id=run_id,
        name="boom",
        status=TaskStatus.QUEUED,
        tool_call=tool_call,
        max_attempts=3,
        created_at_ms=0,
    )

    service = await _build_service(
        tmp_path=tmp_path,
        settings=ReflexorSettings(workspace_root=tmp_path, enabled_scopes=["fs.read"]),
        tool=tool,
        task=task,
        task_repo=_InMemoryTaskRepo(),
        tool_call_repo=_InMemoryToolCallRepo(),
        approval_repo=_InMemoryApprovalRepo(),
        packet_repo=_InMemoryRunPacketRepo(),
        ledger=_FakeLedger(),
        metrics=metrics,
    )

    report = await service.execute_task(task.task_id)
    assert report.disposition == ExecutionDisposition.FAILED_TRANSIENT

    text = generate_latest(metrics.registry).decode()
    assert (
        _metric_value(
            text,
            name="tool_latency_seconds_count",
            labels={"tool_name": tool.manifest.name, "ok": "false"},
        )
        == 1.0
    )
    assert _metric_value(text, name="tasks_completed_total", labels={"status": "failed"}) == 1.0
    assert (
        _metric_value(
            text,
            name="executor_retries_total",
            labels={"tool_name": tool.manifest.name, "error_code": "TOOL_ERROR"},
        )
        == 1.0
    )


@pytest.mark.asyncio
async def test_execute_task_success_queues_newly_ready_dependents(tmp_path: Path) -> None:
    tool = _RecordingTool(result=ToolResult(ok=True, data={"ok": True}), idempotent=True)

    run_id = str(uuid4())
    parent_tool_call = ToolCall(
        tool_call_id=str(uuid4()),
        tool_name=tool.manifest.name,
        args={"text": "parent"},
        permission_scope=tool.manifest.permission_scope,
        idempotency_key="k-parent",
        status=ToolCallStatus.PENDING,
        created_at_ms=0,
    )
    parent_task = Task(
        task_id=str(uuid4()),
        run_id=run_id,
        name="parent",
        status=TaskStatus.QUEUED,
        tool_call=parent_tool_call,
        created_at_ms=0,
    )
    child_tool_call = ToolCall(
        tool_call_id=str(uuid4()),
        tool_name=tool.manifest.name,
        args={"text": "child"},
        permission_scope=tool.manifest.permission_scope,
        idempotency_key="k-child",
        status=ToolCallStatus.PENDING,
        created_at_ms=0,
    )
    child_task = Task(
        task_id=str(uuid4()),
        run_id=run_id,
        name="child",
        status=TaskStatus.PENDING,
        tool_call=child_tool_call,
        depends_on=[parent_task.task_id],
        created_at_ms=0,
    )

    task_repo = _InMemoryTaskRepo()
    tool_call_repo = _InMemoryToolCallRepo()
    queue = _RecordingQueue()
    service = await _build_service(
        tmp_path=tmp_path,
        settings=ReflexorSettings(workspace_root=tmp_path, enabled_scopes=["fs.read"]),
        tool=tool,
        task=parent_task,
        task_repo=task_repo,
        tool_call_repo=tool_call_repo,
        approval_repo=_InMemoryApprovalRepo(),
        packet_repo=_InMemoryRunPacketRepo(),
        ledger=_FakeLedger(),
        queue=queue,
    )
    await task_repo.create(child_task)
    await tool_call_repo.create(child_tool_call)

    report = await service.execute_task(parent_task.task_id)

    assert report.disposition == ExecutionDisposition.SUCCEEDED
    updated_child = await task_repo.get(child_task.task_id)
    assert updated_child is not None
    assert updated_child.status == TaskStatus.QUEUED
    assert len(queue.enqueued) == 1
    assert queue.enqueued[0].task_id == child_task.task_id


@pytest.mark.asyncio
async def test_execute_task_terminal_failure_cancels_blocked_dependents(tmp_path: Path) -> None:
    tool = _RecordingTool(
        result=ToolResult(ok=False, error_code="INVALID_ARGS", error_message="bad args"),
        idempotent=True,
    )

    run_id = str(uuid4())
    parent_tool_call = ToolCall(
        tool_call_id=str(uuid4()),
        tool_name=tool.manifest.name,
        args={"text": "parent"},
        permission_scope=tool.manifest.permission_scope,
        idempotency_key="k-parent",
        status=ToolCallStatus.PENDING,
        created_at_ms=0,
    )
    parent_task = Task(
        task_id=str(uuid4()),
        run_id=run_id,
        name="parent",
        status=TaskStatus.QUEUED,
        tool_call=parent_tool_call,
        created_at_ms=0,
    )
    child_tool_call = ToolCall(
        tool_call_id=str(uuid4()),
        tool_name=tool.manifest.name,
        args={"text": "child"},
        permission_scope=tool.manifest.permission_scope,
        idempotency_key="k-child",
        status=ToolCallStatus.PENDING,
        created_at_ms=0,
    )
    child_task = Task(
        task_id=str(uuid4()),
        run_id=run_id,
        name="child",
        status=TaskStatus.PENDING,
        tool_call=child_tool_call,
        depends_on=[parent_task.task_id],
        created_at_ms=0,
    )

    task_repo = _InMemoryTaskRepo()
    tool_call_repo = _InMemoryToolCallRepo()
    service = await _build_service(
        tmp_path=tmp_path,
        settings=ReflexorSettings(workspace_root=tmp_path, enabled_scopes=["fs.read"]),
        tool=tool,
        task=parent_task,
        task_repo=task_repo,
        tool_call_repo=tool_call_repo,
        approval_repo=_InMemoryApprovalRepo(),
        packet_repo=_InMemoryRunPacketRepo(),
        ledger=_FakeLedger(),
    )
    await task_repo.create(child_task)
    await tool_call_repo.create(child_tool_call)

    report = await service.execute_task(parent_task.task_id)

    assert report.disposition == ExecutionDisposition.FAILED_PERMANENT
    updated_child = await task_repo.get(child_task.task_id)
    updated_child_tool_call = await tool_call_repo.get(child_tool_call.tool_call_id)
    assert updated_child is not None
    assert updated_child.status == TaskStatus.CANCELED
    assert updated_child_tool_call is not None
    assert updated_child_tool_call.status == ToolCallStatus.CANCELED

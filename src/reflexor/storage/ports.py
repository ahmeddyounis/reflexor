from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from reflexor.domain.enums import ApprovalStatus, RunStatus, TaskStatus, ToolCallStatus
from reflexor.domain.models import Approval, Task, ToolCall
from reflexor.domain.models_event import Event
from reflexor.domain.models_run_packet import RunPacket
from reflexor.memory.models import MemoryItem


@dataclass(frozen=True, slots=True)
class RunRecord:
    """Run metadata record (stored separately from RunPacket blobs)."""

    run_id: str
    parent_run_id: str | None
    created_at_ms: int
    started_at_ms: int | None
    completed_at_ms: int | None


@dataclass(frozen=True, slots=True)
class RunSummary:
    """Minimal run summary for admin/API read paths."""

    run_id: str
    created_at_ms: int
    started_at_ms: int | None
    completed_at_ms: int | None
    status: RunStatus
    event_type: str | None
    event_source: str | None
    tasks_total: int
    tasks_pending: int
    tasks_queued: int
    tasks_running: int
    tasks_succeeded: int
    tasks_failed: int
    tasks_canceled: int
    approvals_total: int
    approvals_pending: int


@dataclass(frozen=True, slots=True)
class TaskSummary:
    """Minimal task summary for admin/API read paths."""

    task_id: str
    run_id: str
    name: str
    status: TaskStatus
    attempts: int
    max_attempts: int
    timeout_s: int
    depends_on: list[str]
    created_at_ms: int

    tool_call_id: str | None
    tool_name: str | None
    permission_scope: str | None
    idempotency_key: str | None
    tool_call_status: ToolCallStatus | None


@dataclass(frozen=True, slots=True)
class EventSuppressionRecord:
    """Stored suppression state for an event signature (loop/cascade protection)."""

    signature_hash: str
    event_type: str
    event_source: str
    signature: dict[str, object]
    window_start_ms: int
    count: int
    threshold: int
    window_ms: int
    suppressed_until_ms: int | None
    resume_required: bool
    cleared_at_ms: int | None
    cleared_by: str | None
    cleared_request_id: str | None
    created_at_ms: int
    updated_at_ms: int
    expires_at_ms: int


class EventRepo(Protocol):
    """Event persistence port (domain -> storage boundary)."""

    async def create(self, event: Event) -> Event: ...

    async def get_by_dedupe(self, *, source: str, dedupe_key: str) -> Event | None: ...

    async def create_or_get_by_dedupe(
        self, *, source: str, dedupe_key: str, event: Event
    ) -> tuple[Event, bool]: ...

    async def get(self, event_id: str) -> Event | None: ...

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        event_type: str | None = None,
        source: str | None = None,
    ) -> list[Event]: ...


class EventSuppressionRepo(Protocol):
    """Persistence port for event suppression state keyed by signature hash."""

    async def get(self, signature_hash: str) -> EventSuppressionRecord | None: ...

    async def upsert(self, record: EventSuppressionRecord) -> EventSuppressionRecord: ...

    async def delete(self, signature_hash: str) -> None: ...

    async def count_active(self, *, now_ms: int) -> int: ...

    async def list_active(
        self,
        *,
        now_ms: int,
        limit: int,
        offset: int,
    ) -> list[EventSuppressionRecord]: ...


class RunRepo(Protocol):
    """Run metadata persistence port."""

    async def create(self, run: RunRecord) -> RunRecord: ...

    async def get(self, run_id: str) -> RunRecord | None: ...

    async def update_timestamps(
        self,
        run_id: str,
        *,
        started_at_ms: int | None = None,
        completed_at_ms: int | None = None,
    ) -> RunRecord: ...

    async def list_recent(self, *, limit: int, offset: int) -> list[RunRecord]: ...

    async def list_summaries(
        self,
        *,
        limit: int,
        offset: int,
        status: RunStatus | None = None,
        created_after_ms: int | None = None,
        created_before_ms: int | None = None,
    ) -> list[RunSummary]: ...

    async def count_summaries(
        self,
        *,
        status: RunStatus | None = None,
        created_after_ms: int | None = None,
        created_before_ms: int | None = None,
    ) -> int: ...

    async def get_summary(self, run_id: str) -> RunSummary | None: ...


class TaskRepo(Protocol):
    """Task persistence port."""

    async def create(self, task: Task) -> Task: ...

    async def get(self, task_id: str) -> Task | None: ...

    async def update_status(self, task_id: str, status: TaskStatus) -> Task: ...

    async def update(self, task: Task) -> Task: ...

    async def list_by_run(self, run_id: str) -> list[Task]: ...

    async def list_summaries(
        self,
        *,
        limit: int,
        offset: int,
        run_id: str | None = None,
        status: TaskStatus | None = None,
    ) -> list[TaskSummary]: ...

    async def count_summaries(
        self,
        *,
        run_id: str | None = None,
        status: TaskStatus | None = None,
    ) -> int: ...

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        run_id: str | None = None,
        status: TaskStatus | None = None,
    ) -> list[Task]: ...


class ToolCallRepo(Protocol):
    """ToolCall persistence port."""

    async def create(self, tool_call: ToolCall) -> ToolCall: ...

    async def get(self, tool_call_id: str) -> ToolCall | None: ...

    async def get_by_idempotency_key(self, idempotency_key: str) -> ToolCall | None: ...

    async def update_status(self, tool_call_id: str, status: ToolCallStatus) -> ToolCall: ...

    async def update(self, tool_call: ToolCall) -> ToolCall: ...

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        status: ToolCallStatus | None = None,
    ) -> list[ToolCall]: ...


class ApprovalRepo(Protocol):
    """Approval persistence port (HITL gate state)."""

    async def create(self, approval: Approval) -> Approval: ...

    async def get(self, approval_id: str) -> Approval | None: ...

    async def get_by_tool_call(self, tool_call_id: str) -> Approval | None: ...

    async def update_status(
        self,
        approval_id: str,
        status: ApprovalStatus,
        *,
        decided_at_ms: int | None = None,
        decided_by: str | None = None,
    ) -> Approval: ...

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        status: ApprovalStatus | None = None,
        run_id: str | None = None,
    ) -> list[Approval]: ...

    async def count(
        self,
        *,
        status: ApprovalStatus | None = None,
        run_id: str | None = None,
    ) -> int: ...


class RunPacketRepo(Protocol):
    """RunPacket persistence port (audit/replay envelope storage)."""

    async def create(self, packet: RunPacket) -> RunPacket: ...

    async def get(self, run_id: str) -> RunPacket | None: ...

    async def list_recent(self, *, limit: int, offset: int) -> list[RunPacket]: ...

    async def get_run_id_for_event(self, event_id: str) -> str | None: ...


class MemoryRepo(Protocol):
    """Persistence port for planner memory summaries."""

    async def upsert(self, item: MemoryItem) -> MemoryItem: ...

    async def get_by_run(self, run_id: str) -> MemoryItem | None: ...

    async def list_recent(
        self,
        *,
        limit: int,
        offset: int = 0,
        event_type: str | None = None,
        event_source: str | None = None,
    ) -> list[MemoryItem]: ...


if TYPE_CHECKING:

    class _MockToolCallRepo:
        async def create(self, tool_call: ToolCall) -> ToolCall: ...

        async def get(self, tool_call_id: str) -> ToolCall | None: ...

        async def get_by_idempotency_key(self, idempotency_key: str) -> ToolCall | None: ...

        async def update_status(self, tool_call_id: str, status: ToolCallStatus) -> ToolCall: ...

        async def update(self, tool_call: ToolCall) -> ToolCall: ...

        async def list(
            self, *, limit: int, offset: int, status: ToolCallStatus | None = None
        ) -> list[ToolCall]: ...

    _tool_call_repo: ToolCallRepo = _MockToolCallRepo()


__all__ = [
    "ApprovalRepo",
    "EventRepo",
    "EventSuppressionRecord",
    "EventSuppressionRepo",
    "MemoryRepo",
    "RunPacketRepo",
    "RunRecord",
    "RunRepo",
    "RunSummary",
    "TaskRepo",
    "ToolCallRepo",
]

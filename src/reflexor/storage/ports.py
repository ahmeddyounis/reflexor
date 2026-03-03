from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from reflexor.domain.enums import ApprovalStatus, RunStatus, TaskStatus, ToolCallStatus
from reflexor.domain.models import Approval, Task, ToolCall
from reflexor.domain.models_event import Event
from reflexor.domain.models_run_packet import RunPacket


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
    ) -> list[Approval]: ...


class RunPacketRepo(Protocol):
    """RunPacket persistence port (audit/replay envelope storage)."""

    async def create(self, packet: RunPacket) -> RunPacket: ...

    async def get(self, run_id: str) -> RunPacket | None: ...

    async def list_recent(self, *, limit: int, offset: int) -> list[RunPacket]: ...

    async def get_run_id_for_event(self, event_id: str) -> str | None: ...


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
    "RunPacketRepo",
    "RunRecord",
    "RunRepo",
    "RunSummary",
    "TaskRepo",
    "ToolCallRepo",
]

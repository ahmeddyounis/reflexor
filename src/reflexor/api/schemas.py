"""Public API schemas (request/response DTOs).

These models are part of the API contract and should remain stable and JSON-friendly.

Guidelines:
- Do not return ORM objects.
- Prefer explicit DTOs over reusing internal domain models.
- Keep pagination bounded (limit <= 200) to discourage expensive/unbounded endpoints.
"""

from __future__ import annotations

from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from reflexor.domain.enums import ApprovalStatus, RunStatus, TaskStatus, ToolCallStatus

T = TypeVar("T")

MAX_PAGE_LIMIT = 200
DEFAULT_PAGE_LIMIT = 50


class ErrorPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    message: str


class ErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    error: ErrorPayload


class Page(BaseModel, Generic[T]):
    model_config = ConfigDict(extra="forbid", frozen=True)

    limit: int = Field(default=DEFAULT_PAGE_LIMIT, ge=0, le=MAX_PAGE_LIMIT)
    offset: int = Field(default=0, ge=0)
    total: int = Field(default=0, ge=0)
    items: list[T] = Field(default_factory=list)


class SubmitEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: str
    source: str
    payload: dict[str, object] = Field(default_factory=dict)
    dedupe_key: str | None = None
    received_at_ms: int | None = None


class SubmitEventResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ok: bool = True
    event_id: str
    run_id: str | None
    duplicate: bool = False


class RunSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    created_at_ms: int
    started_at_ms: int | None = None
    completed_at_ms: int | None = None
    status: RunStatus
    event_type: str | None = None
    event_source: str | None = None

    tasks_total: int
    tasks_pending: int
    tasks_queued: int
    tasks_running: int
    tasks_succeeded: int
    tasks_failed: int
    tasks_canceled: int

    approvals_total: int
    approvals_pending: int


class RunDetail(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: RunSummary
    run_packet: dict[str, object]


class TaskSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str
    run_id: str
    name: str
    status: TaskStatus
    attempts: int
    max_attempts: int
    timeout_s: int
    depends_on: list[str] = Field(default_factory=list)

    tool_call_id: str | None = None
    tool_name: str | None = None
    permission_scope: str | None = None
    idempotency_key: str | None = None
    tool_call_status: ToolCallStatus | None = None


class ApprovalSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    approval_id: str
    run_id: str
    task_id: str
    tool_call_id: str
    status: ApprovalStatus
    created_at_ms: int
    decided_at_ms: int | None = None
    decided_by: str | None = None
    payload_hash: str | None = None
    preview: str | None = None


class ApprovalDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: Literal["approved", "denied"]
    decided_by: str | None = None


class ApprovalDecisionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    approval: ApprovalSummary


__all__ = [
    "ApprovalDecisionRequest",
    "ApprovalDecisionResponse",
    "ApprovalSummary",
    "DEFAULT_PAGE_LIMIT",
    "ErrorPayload",
    "ErrorResponse",
    "MAX_PAGE_LIMIT",
    "Page",
    "RunDetail",
    "RunSummary",
    "SubmitEventRequest",
    "SubmitEventResponse",
    "TaskSummary",
]

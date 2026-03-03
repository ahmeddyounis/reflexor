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


class ErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    error_code: str
    message: str
    request_id: str
    details: dict[str, object] | None = None


class Page(BaseModel, Generic[T]):
    model_config = ConfigDict(extra="forbid", frozen=True)

    limit: int = Field(default=DEFAULT_PAGE_LIMIT, ge=0, le=MAX_PAGE_LIMIT)
    offset: int = Field(default=0, ge=0)
    total: int = Field(default=0, ge=0)
    items: list[T] = Field(default_factory=list)


class SubmitEventRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        json_schema_extra={
            "examples": [
                {
                    "type": "webhook",
                    "source": "github",
                    "payload": {"url": "https://example.com/hook", "action": "opened"},
                    "dedupe_key": "github:delivery:123",
                    "received_at_ms": 1710000000000,
                }
            ]
        },
    )

    type: str
    source: str
    payload: dict[str, object] = Field(default_factory=dict)
    dedupe_key: str | None = None
    received_at_ms: int | None = None


class SubmitEventResponse(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        json_schema_extra={
            "examples": [
                {
                    "ok": True,
                    "event_id": "b7c3d0d4-7b9b-4c61-90f8-6c7df7b60d7a",
                    "run_id": "8f5b84d8-6d0c-4f4a-8a5b-5f4e2c1d7c6a",
                    "duplicate": False,
                }
            ]
        },
    )

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
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        json_schema_extra={
            "examples": [
                {"decision": "approved", "decided_by": "operator@example.com"},
                {"decision": "denied", "decided_by": "operator@example.com"},
            ]
        },
    )

    decision: Literal["approved", "denied"]
    decided_by: str | None = None


class ApprovalActionRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        json_schema_extra={"examples": [{"decided_by": "operator@example.com"}]},
    )

    decided_by: str | None = None


class ApprovalDecisionResponse(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        json_schema_extra={
            "examples": [
                {
                    "approval": {
                        "approval_id": "1f7a19c4-67c0-4d24-b5fb-1d16b5a803dd",
                        "run_id": "8f5b84d8-6d0c-4f4a-8a5b-5f4e2c1d7c6a",
                        "task_id": "bc2b5f2b-0f7a-4e1d-9d9d-2d1c3f7eaa11",
                        "tool_call_id": "4ad2c97a-6ac5-4b72-a7c6-7c8c0b81c3ad",
                        "status": "approved",
                        "created_at_ms": 1710000000000,
                        "decided_at_ms": 1710000001000,
                        "decided_by": "operator@example.com",
                        "payload_hash": "sha256:deadbeef...",
                        "preview": "redacted preview",
                    }
                }
            ]
        },
    )

    approval: ApprovalSummary


__all__ = [
    "ApprovalActionRequest",
    "ApprovalDecisionRequest",
    "ApprovalDecisionResponse",
    "ApprovalSummary",
    "DEFAULT_PAGE_LIMIT",
    "ErrorResponse",
    "MAX_PAGE_LIMIT",
    "Page",
    "RunDetail",
    "RunSummary",
    "SubmitEventRequest",
    "SubmitEventResponse",
    "TaskSummary",
]

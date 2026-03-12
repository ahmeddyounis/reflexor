from __future__ import annotations

import json
import time
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator

from reflexor.domain.enums import ApprovalStatus, TaskStatus, ToolCallStatus

DEFAULT_MAX_APPROVAL_PREVIEW_CHARS = 1_000


def _ensure_jsonable(value: object, *, field_name: str) -> None:
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be valid JSON") from exc


class ToolCall(BaseModel):
    tool_call_id: str = Field(default_factory=lambda: str(uuid4()))
    tool_name: str
    args: dict[str, object] = Field(default_factory=dict)
    permission_scope: str
    idempotency_key: str
    status: ToolCallStatus = ToolCallStatus.PENDING
    created_at_ms: int = Field(default_factory=lambda: int(time.time() * 1000))
    started_at_ms: int | None = None
    completed_at_ms: int | None = None
    result_ref: str | None = None

    @field_validator("tool_call_id", mode="before")
    @classmethod
    def _validate_tool_call_id(cls, value: object) -> str:
        if value is None:
            return str(uuid4())

        if isinstance(value, UUID):
            parsed = value
        elif isinstance(value, str):
            try:
                parsed = UUID(value)
            except ValueError as exc:
                raise ValueError("tool_call_id must be a valid UUID") from exc
        else:
            raise TypeError("tool_call_id must be a UUID or UUID string")

        if parsed.version != 4:
            raise ValueError("tool_call_id must be a UUID4")

        return str(parsed)

    @field_validator("tool_name")
    @classmethod
    def _validate_tool_name(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("tool_name must be non-empty")
        return trimmed

    @field_validator("permission_scope")
    @classmethod
    def _validate_permission_scope(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("permission_scope must be non-empty")
        return trimmed

    @field_validator("idempotency_key")
    @classmethod
    def _validate_idempotency_key(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("idempotency_key must be non-empty")
        return trimmed

    @field_validator("result_ref")
    @classmethod
    def _normalize_result_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = value.strip()
        return trimmed or None

    @field_validator("args")
    @classmethod
    def _validate_args(cls, value: dict[str, object]) -> dict[str, object]:
        _ensure_jsonable(value, field_name="args")
        return value

    @model_validator(mode="after")
    def _validate_timestamps(self) -> ToolCall:
        if self.started_at_ms is not None and self.started_at_ms < self.created_at_ms:
            raise ValueError("started_at_ms must be >= created_at_ms")
        if self.completed_at_ms is not None:
            if self.started_at_ms is not None and self.completed_at_ms < self.started_at_ms:
                raise ValueError("completed_at_ms must be >= started_at_ms")
            if self.completed_at_ms < self.created_at_ms:
                raise ValueError("completed_at_ms must be >= created_at_ms")
        return self


class Task(BaseModel):
    task_id: str = Field(default_factory=lambda: str(uuid4()))
    run_id: str
    name: str
    status: TaskStatus = TaskStatus.PENDING
    tool_call: ToolCall | None = None
    attempts: int = 0
    max_attempts: int = 1
    timeout_s: int = 60
    depends_on: list[str] = Field(default_factory=list)
    created_at_ms: int = Field(default_factory=lambda: int(time.time() * 1000))
    started_at_ms: int | None = None
    completed_at_ms: int | None = None
    labels: list[str] = Field(default_factory=list)
    metadata: dict[str, object] = Field(default_factory=dict)

    @field_validator("task_id", mode="before")
    @classmethod
    def _validate_task_id(cls, value: object) -> str:
        if value is None:
            return str(uuid4())

        if isinstance(value, UUID):
            parsed = value
        elif isinstance(value, str):
            try:
                parsed = UUID(value)
            except ValueError as exc:
                raise ValueError("task_id must be a valid UUID") from exc
        else:
            raise TypeError("task_id must be a UUID or UUID string")

        if parsed.version != 4:
            raise ValueError("task_id must be a UUID4")

        return str(parsed)

    @field_validator("run_id", mode="before")
    @classmethod
    def _validate_run_id(cls, value: object) -> str:
        if value is None:
            raise ValueError("run_id is required")

        if isinstance(value, UUID):
            parsed = value
        elif isinstance(value, str):
            try:
                parsed = UUID(value)
            except ValueError as exc:
                raise ValueError("run_id must be a valid UUID") from exc
        else:
            raise TypeError("run_id must be a UUID or UUID string")

        if parsed.version != 4:
            raise ValueError("run_id must be a UUID4")

        return str(parsed)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("name must be non-empty")
        return trimmed

    @field_validator("timeout_s")
    @classmethod
    def _validate_timeout_s(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("timeout_s must be > 0")
        return value

    @field_validator("attempts")
    @classmethod
    def _validate_attempts(cls, value: int) -> int:
        if value < 0:
            raise ValueError("attempts must be >= 0")
        return value

    @field_validator("max_attempts")
    @classmethod
    def _validate_max_attempts(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("max_attempts must be > 0")
        return value

    @field_validator("depends_on")
    @classmethod
    def _validate_depends_on(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for item in value:
            trimmed = item.strip()
            if not trimmed:
                raise ValueError("depends_on entries must be non-empty")
            normalized.append(trimmed)
        return normalized

    @field_validator("labels")
    @classmethod
    def _validate_labels(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for item in value:
            trimmed = item.strip()
            if not trimmed:
                raise ValueError("labels entries must be non-empty")
            normalized.append(trimmed)
        return normalized

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(cls, value: dict[str, object]) -> dict[str, object]:
        _ensure_jsonable(value, field_name="metadata")
        return value

    @model_validator(mode="after")
    def _validate_attempt_limits(self) -> Task:
        if self.attempts > self.max_attempts:
            raise ValueError("attempts must be <= max_attempts")
        return self

    @model_validator(mode="after")
    def _validate_timestamps(self) -> Task:
        if self.started_at_ms is not None and self.started_at_ms < self.created_at_ms:
            raise ValueError("started_at_ms must be >= created_at_ms")
        if self.completed_at_ms is not None:
            if self.started_at_ms is not None and self.completed_at_ms < self.started_at_ms:
                raise ValueError("completed_at_ms must be >= started_at_ms")
            if self.completed_at_ms < self.created_at_ms:
                raise ValueError("completed_at_ms must be >= created_at_ms")
        return self


class Approval(BaseModel):
    approval_id: str = Field(default_factory=lambda: str(uuid4()))
    run_id: str
    task_id: str
    tool_call_id: str
    status: ApprovalStatus = ApprovalStatus.PENDING
    created_at_ms: int = Field(default_factory=lambda: int(time.time() * 1000))
    decided_at_ms: int | None = None
    decided_by: str | None = None
    payload_hash: str | None = None
    preview: str | None = None

    @field_validator("approval_id", mode="before")
    @classmethod
    def _validate_approval_id(cls, value: object) -> str:
        if value is None:
            return str(uuid4())

        if isinstance(value, UUID):
            parsed = value
        elif isinstance(value, str):
            try:
                parsed = UUID(value)
            except ValueError as exc:
                raise ValueError("approval_id must be a valid UUID") from exc
        else:
            raise TypeError("approval_id must be a UUID or UUID string")

        if parsed.version != 4:
            raise ValueError("approval_id must be a UUID4")

        return str(parsed)

    @field_validator("run_id", "task_id", "tool_call_id", mode="before")
    @classmethod
    def _validate_required_uuid4_str(cls, value: object, info: ValidationInfo) -> str:
        field_name = info.field_name
        if value is None:
            raise ValueError(f"{field_name} is required")

        if isinstance(value, UUID):
            parsed = value
        elif isinstance(value, str):
            try:
                parsed = UUID(value)
            except ValueError as exc:
                raise ValueError(f"{field_name} must be a valid UUID") from exc
        else:
            raise TypeError(f"{field_name} must be a UUID or UUID string")

        if parsed.version != 4:
            raise ValueError(f"{field_name} must be a UUID4")

        return str(parsed)

    @field_validator("status")
    @classmethod
    def _validate_status(cls, value: ApprovalStatus) -> ApprovalStatus:
        if value not in {ApprovalStatus.PENDING, ApprovalStatus.APPROVED, ApprovalStatus.DENIED}:
            raise ValueError("status must be one of: pending, approved, denied")
        return value

    @field_validator("decided_by")
    @classmethod
    def _normalize_decided_by(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = value.strip()
        return trimmed or None

    @field_validator("payload_hash")
    @classmethod
    def _normalize_payload_hash(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = value.strip()
        return trimmed or None

    @field_validator("preview")
    @classmethod
    def _normalize_preview(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = value.strip()
        if not trimmed:
            return None
        return trimmed[:DEFAULT_MAX_APPROVAL_PREVIEW_CHARS]

    @model_validator(mode="after")
    def _validate_decision_fields(self) -> Approval:
        if self.decided_at_ms is not None and self.decided_at_ms < self.created_at_ms:
            raise ValueError("decided_at_ms must be >= created_at_ms")

        if self.status == ApprovalStatus.PENDING:
            if self.decided_at_ms is not None:
                raise ValueError("decided_at_ms must be null when status is pending")
            if self.decided_by is not None:
                raise ValueError("decided_by must be null when status is pending")
        else:
            if self.decided_at_ms is None:
                raise ValueError("decided_at_ms is required when status is approved/denied")

        return self

    def approve(self, decided_by: str | None = None, decided_at_ms: int | None = None) -> Approval:
        return self._decide(
            ApprovalStatus.APPROVED, decided_by=decided_by, decided_at_ms=decided_at_ms
        )

    def deny(self, decided_by: str | None = None, decided_at_ms: int | None = None) -> Approval:
        return self._decide(
            ApprovalStatus.DENIED, decided_by=decided_by, decided_at_ms=decided_at_ms
        )

    def _decide(
        self,
        status: ApprovalStatus,
        *,
        decided_by: str | None,
        decided_at_ms: int | None,
    ) -> Approval:
        if self.status != ApprovalStatus.PENDING:
            raise ValueError("approval has already been decided")

        now_ms = int(time.time() * 1000)
        decided_at = decided_at_ms if decided_at_ms is not None else now_ms

        normalized_decided_by = decided_by.strip() if decided_by is not None else None
        if normalized_decided_by == "":
            normalized_decided_by = None

        updated = self.model_dump()
        updated.update(
            {
                "status": status,
                "decided_at_ms": decided_at,
                "decided_by": normalized_decided_by,
            }
        )
        return Approval.model_validate(updated)

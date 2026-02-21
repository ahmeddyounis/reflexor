from __future__ import annotations

import json
import time
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from reflexor.domain.enums import ApprovalStatus, TaskStatus, ToolCallStatus


class Task(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    title: str
    status: TaskStatus = TaskStatus.PENDING


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
        try:
            json.dumps(value, ensure_ascii=False)
        except TypeError as exc:
            raise ValueError("args must be JSON-serializable") from exc
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


class Approval(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    status: ApprovalStatus = ApprovalStatus.PENDING

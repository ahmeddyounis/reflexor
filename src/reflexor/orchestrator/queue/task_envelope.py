from __future__ import annotations

import json
import time
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator


class TaskEnvelope(BaseModel):
    """Queue message contract for task execution.

    This model is intentionally JSON-only (no ORM/DB types) and stable for future queue backends.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    envelope_id: str = Field(default_factory=lambda: str(uuid4()))
    task_id: str
    run_id: str

    attempt: int = 0

    created_at_ms: int | None = None
    available_at_ms: int | None = None

    priority: int | None = None
    correlation_ids: dict[str, str | None] | None = None
    trace: dict[str, object] | None = None
    payload: dict[str, object] | None = None

    @model_validator(mode="before")
    @classmethod
    def _populate_default_timestamps(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data

        normalized = dict(data)
        now_ms = int(time.time() * 1000)
        if normalized.get("created_at_ms") is None:
            normalized["created_at_ms"] = now_ms
        if normalized.get("available_at_ms") is None:
            normalized["available_at_ms"] = normalized["created_at_ms"]
        return normalized

    @field_validator("envelope_id", "task_id", "run_id", mode="before")
    @classmethod
    def _validate_uuid4_strs(cls, value: object, info: ValidationInfo) -> str:
        field_name = info.field_name or "id"
        if value is None:
            if field_name == "envelope_id":
                return str(uuid4())
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

    @field_validator("attempt")
    @classmethod
    def _validate_attempt(cls, value: int) -> int:
        if value < 0:
            raise ValueError("attempt must be >= 0")
        return value

    @field_validator("priority")
    @classmethod
    def _validate_priority(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if value < 0:
            raise ValueError("priority must be >= 0")
        return int(value)

    @field_validator("created_at_ms", "available_at_ms")
    @classmethod
    def _validate_timestamps_required(cls, value: int | None, info: ValidationInfo) -> int:
        field_name = info.field_name or "timestamp"
        if value is None:
            raise ValueError(f"{field_name} is required")
        if value < 0:
            raise ValueError(f"{field_name} must be >= 0")
        return int(value)

    @field_validator("correlation_ids")
    @classmethod
    def _validate_correlation_ids(
        cls, value: dict[str, str | None] | None
    ) -> dict[str, str | None] | None:
        if value is None:
            return None

        normalized: dict[str, str | None] = {}
        for key, raw in value.items():
            normalized_key = str(key).strip()
            if not normalized_key:
                raise ValueError("correlation_ids keys must be non-empty")
            if raw is None:
                normalized_value = None
            else:
                normalized_value = str(raw).strip() or None
            normalized[normalized_key] = normalized_value

        return normalized

    @field_validator("trace", "payload")
    @classmethod
    def _validate_optional_json_dicts(
        cls, value: dict[str, object] | None, info: ValidationInfo
    ) -> dict[str, object] | None:
        if value is None:
            return None
        field_name = info.field_name or "value"
        try:
            json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field_name} must be JSON-serializable") from exc
        return value

    @model_validator(mode="after")
    def _validate_timestamp_order(self) -> TaskEnvelope:
        assert self.created_at_ms is not None
        assert self.available_at_ms is not None
        if self.available_at_ms < self.created_at_ms:
            raise ValueError("available_at_ms must be >= created_at_ms")
        return self

    def is_available(self, *, now_ms: int) -> bool:
        """Return True if the envelope is eligible for reservation/execution."""

        return now_ms >= (self.available_at_ms or 0)

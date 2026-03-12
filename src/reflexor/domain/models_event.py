from __future__ import annotations

import json
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, ValidationInfo, field_validator

DEFAULT_MAX_PAYLOAD_KEYS = 200
DEFAULT_MAX_PAYLOAD_BYTES = 64_000


def _count_payload_keys(value: object) -> int:
    count = 0
    stack: list[object] = [value]

    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            count += len(current)
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)

    return count


def _payload_bytes(value: object) -> int:
    payload_json = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )
    return len(payload_json.encode("utf-8"))


class Event(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    type: str
    source: str
    received_at_ms: int
    payload: dict[str, object]
    dedupe_key: str | None = None

    @field_validator("event_id", mode="before")
    @classmethod
    def _validate_event_id(cls, value: object) -> str:
        if value is None:
            return str(uuid4())

        if isinstance(value, UUID):
            parsed = value
        elif isinstance(value, str):
            try:
                parsed = UUID(value)
            except ValueError as exc:  # pragma: no cover
                raise ValueError("event_id must be a valid UUID") from exc
        else:
            raise TypeError("event_id must be a UUID or UUID string")

        if parsed.version != 4:
            raise ValueError("event_id must be a UUID4")

        return str(parsed)

    @field_validator("type")
    @classmethod
    def _validate_type(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("type must be non-empty")
        return trimmed

    @field_validator("source")
    @classmethod
    def _validate_source(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("source must be non-empty")
        return trimmed

    @field_validator("dedupe_key")
    @classmethod
    def _normalize_dedupe_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = value.strip()
        return trimmed or None

    @field_validator("payload")
    @classmethod
    def _validate_payload(cls, value: dict[str, object], info: ValidationInfo) -> dict[str, object]:
        key_count = _count_payload_keys(value)
        if key_count > DEFAULT_MAX_PAYLOAD_KEYS:
            raise ValueError(
                f"payload has too many keys ({key_count}); max is {DEFAULT_MAX_PAYLOAD_KEYS}"
            )

        max_bytes = DEFAULT_MAX_PAYLOAD_BYTES
        if info.context is not None and "max_payload_bytes" in info.context:
            raw_max = info.context["max_payload_bytes"]
            max_bytes = int(raw_max)
            if max_bytes <= 0:  # pragma: no cover
                raise ValueError("max_payload_bytes must be > 0")

        try:
            size_bytes = _payload_bytes(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("payload must be valid JSON") from exc

        if size_bytes > max_bytes:
            raise ValueError(f"payload is too large ({size_bytes} bytes); max is {max_bytes}")

        return value

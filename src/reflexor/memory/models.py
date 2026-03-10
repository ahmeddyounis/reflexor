from __future__ import annotations

import json
import time
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator


class MemoryItem(BaseModel):
    memory_id: str = Field(default_factory=lambda: str(uuid4()))
    run_id: str
    event_id: str | None = None
    kind: str = "run_summary"
    event_type: str | None = None
    event_source: str | None = None
    summary: str
    content: dict[str, object] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    created_at_ms: int = Field(default_factory=lambda: int(time.time() * 1000))
    updated_at_ms: int = Field(default_factory=lambda: int(time.time() * 1000))

    @field_validator("memory_id", "run_id", "event_id", mode="before")
    @classmethod
    def _validate_uuid_fields(cls, value: object, info: object) -> str | None:
        field_name = getattr(info, "field_name", None) or "value"
        if value is None:
            return None
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

    @field_validator("kind", "summary", "event_type", "event_source")
    @classmethod
    def _normalize_optional_strings(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        trimmed = value.strip()
        if not trimmed:
            field_name = getattr(info, "field_name", None) or "value"
            raise ValueError(f"{field_name} must be non-empty")
        return trimmed

    @field_validator("content")
    @classmethod
    def _validate_content_json(cls, value: dict[str, object]) -> dict[str, object]:
        try:
            json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except TypeError as exc:
            raise ValueError("content must be JSON-serializable") from exc
        return value

    @field_validator("tags")
    @classmethod
    def _validate_tags(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            trimmed = str(item).strip()
            if not trimmed:
                raise ValueError("tags entries must be non-empty")
            if trimmed in seen:
                continue
            seen.add(trimmed)
            normalized.append(trimmed)
        return normalized

    def to_planning_dict(self) -> dict[str, object]:
        return {
            "memory_id": self.memory_id,
            "run_id": self.run_id,
            "event_id": self.event_id,
            "kind": self.kind,
            "event_type": self.event_type,
            "event_source": self.event_source,
            "summary": self.summary,
            "content": self.content,
            "tags": list(self.tags),
            "updated_at_ms": self.updated_at_ms,
        }


__all__ = ["MemoryItem"]

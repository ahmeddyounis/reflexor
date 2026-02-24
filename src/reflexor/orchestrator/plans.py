"""Orchestrator planning contracts (typed, JSON-safe).

This module defines the planning-facing data contracts used by orchestrator components.

Clean Architecture:
- Orchestrator is application-layer code.
- This module may depend on `reflexor.domain` and stdlib.
- Forbidden: FastAPI/SQLAlchemy/httpx/worker/API/CLI imports.
"""

from __future__ import annotations

import json
import time
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from reflexor.domain.models_event import Event


class ProposedTask(BaseModel):
    """A proposed task emitted by a reflex/planner.

    This is an orchestrator-level contract (not the domain `Task`).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    tool_name: str
    args: dict[str, object] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    timeout_s: int = 60
    max_attempts: int = 1
    priority: int | None = None
    idempotency_seed: str | None = None

    @field_validator("name", "tool_name")
    @classmethod
    def _validate_non_empty_strs(cls, value: str, info: object) -> str:
        trimmed = value.strip()
        if not trimmed:
            field_name = getattr(info, "field_name", None) or "value"
            raise ValueError(f"{field_name} must be non-empty")
        return trimmed

    @field_validator("idempotency_seed")
    @classmethod
    def _normalize_idempotency_seed(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = value.strip()
        return trimmed or None

    @field_validator("timeout_s")
    @classmethod
    def _validate_timeout_s(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("timeout_s must be > 0")
        return int(value)

    @field_validator("max_attempts")
    @classmethod
    def _validate_max_attempts(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("max_attempts must be > 0")
        return int(value)

    @field_validator("priority")
    @classmethod
    def _validate_priority(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if value < 0:
            raise ValueError("priority must be >= 0")
        return int(value)

    @field_validator("depends_on")
    @classmethod
    def _validate_depends_on(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for item in value:
            trimmed = str(item).strip()
            if not trimmed:
                raise ValueError("depends_on entries must be non-empty")
            normalized.append(trimmed)
        return normalized

    @field_validator("args")
    @classmethod
    def _validate_args_json(cls, value: dict[str, object]) -> dict[str, object]:
        try:
            json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise ValueError("args must be JSON-serializable") from exc
        return value


class Plan(BaseModel):
    """Planner output: a concrete plan containing proposed tasks."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    plan_id: str = Field(default_factory=lambda: str(uuid4()))
    summary: str
    tasks: list[ProposedTask] = Field(default_factory=list)
    metadata: dict[str, object] = Field(default_factory=dict)

    @field_validator("plan_id", mode="before")
    @classmethod
    def _validate_plan_id(cls, value: object) -> str:
        if value is None:
            return str(uuid4())

        if isinstance(value, UUID):
            parsed = value
        elif isinstance(value, str):
            try:
                parsed = UUID(value)
            except ValueError as exc:
                raise ValueError("plan_id must be a valid UUID") from exc
        else:
            raise TypeError("plan_id must be a UUID or UUID string")

        if parsed.version != 4:
            raise ValueError("plan_id must be a UUID4")

        return str(parsed)

    @field_validator("summary")
    @classmethod
    def _validate_summary(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("summary must be non-empty")
        return trimmed

    @field_validator("metadata")
    @classmethod
    def _validate_metadata_json(cls, value: dict[str, object]) -> dict[str, object]:
        try:
            json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise ValueError("metadata must be JSON-serializable") from exc
        return value


class LimitsSnapshot(BaseModel):
    """A snapshot of effective run limits provided to planning/routing logic."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_tasks: int | None = None
    max_tool_calls: int | None = None
    max_runtime_s: float | None = None

    @field_validator("max_tasks", "max_tool_calls")
    @classmethod
    def _validate_optional_positive_ints(cls, value: int | None, info: object) -> int | None:
        if value is None:
            return None
        field_name = getattr(info, "field_name", None) or "value"
        if value <= 0:
            raise ValueError(f"{field_name} must be > 0")
        return int(value)

    @field_validator("max_runtime_s")
    @classmethod
    def _validate_optional_positive_float(cls, value: float | None) -> float | None:
        if value is None:
            return None
        runtime_s = float(value)
        if runtime_s <= 0:
            raise ValueError("max_runtime_s must be > 0")
        return runtime_s


class PlanningInput(BaseModel):
    """Input to the planner.

    Planning can be triggered by a periodic tick (no events) or by one or more events.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    trigger: Literal["tick", "event"]
    events: list[Event] = Field(default_factory=list)
    limits: LimitsSnapshot = Field(default_factory=LimitsSnapshot)
    now_ms: int = Field(default_factory=lambda: int(time.time() * 1000))

    @field_validator("now_ms")
    @classmethod
    def _validate_now_ms(cls, value: int) -> int:
        if value < 0:
            raise ValueError("now_ms must be >= 0")
        return int(value)

    @model_validator(mode="after")
    def _validate_trigger_requires_event(self) -> PlanningInput:
        if self.trigger == "event" and not self.events:
            raise ValueError("events must be non-empty when trigger='event'")
        return self


class ReflexDecision(BaseModel):
    """Output of reflex routing: what to do next for an event."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action: Literal["fast_tasks", "needs_planning", "drop"]
    reason: str
    proposed_tasks: list[ProposedTask] = Field(default_factory=list)

    @field_validator("reason")
    @classmethod
    def _validate_reason(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("reason must be non-empty")
        return trimmed


__all__ = [
    "LimitsSnapshot",
    "Plan",
    "PlanningInput",
    "ProposedTask",
    "ReflexDecision",
]

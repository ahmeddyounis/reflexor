from __future__ import annotations

import json
import time
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from reflexor.domain.models import Task
from reflexor.domain.models_event import Event

DEFAULT_MAX_REFLEX_DECISION_BYTES = 32_000
DEFAULT_MAX_PLAN_BYTES = 128_000
DEFAULT_MAX_TOOL_RESULT_BYTES = 64_000
DEFAULT_MAX_POLICY_DECISION_BYTES = 32_000
DEFAULT_MAX_PACKET_BYTES = 512_000
DEFAULT_MAX_TASKS = 500


def _json_bytes(value: object) -> int:
    payload_json = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return len(payload_json.encode("utf-8"))


def _uuid4_str(value: object, *, field_name: str, allow_none: bool = False) -> str | None:
    if value is None:
        if allow_none:
            return None
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


class RunPacket(BaseModel):
    """Audit/replay envelope for a single run.

    This model is intended to be an immutable-ish record. Prefer creating new instances
    (e.g., via the `with_*` helpers) rather than mutating lists in place.

    Size caps exist to discourage embedding raw tool outputs directly. Store summaries
    and references (e.g., `result_ref`) instead of large blobs.
    """

    model_config = ConfigDict(frozen=True)

    run_id: str
    parent_run_id: str | None = None
    event: Event
    reflex_decision: dict[str, object] = Field(default_factory=dict)
    plan: dict[str, object] = Field(default_factory=dict)
    tasks: list[Task] = Field(default_factory=list)
    tool_results: list[dict[str, object]] = Field(default_factory=list)
    policy_decisions: list[dict[str, object]] = Field(default_factory=list)
    created_at_ms: int = Field(default_factory=lambda: int(time.time() * 1000))
    started_at_ms: int | None = None
    completed_at_ms: int | None = None

    @field_validator("run_id", mode="before")
    @classmethod
    def _validate_run_id(cls, value: object) -> str:
        validated = _uuid4_str(value, field_name="run_id")
        assert validated is not None
        return validated

    @field_validator("parent_run_id", mode="before")
    @classmethod
    def _validate_parent_run_id(cls, value: object) -> str | None:
        return _uuid4_str(value, field_name="parent_run_id", allow_none=True)

    @field_validator("reflex_decision")
    @classmethod
    def _validate_reflex_decision(cls, value: dict[str, object]) -> dict[str, object]:
        try:
            size_bytes = _json_bytes(value)
        except TypeError as exc:
            raise ValueError("reflex_decision must be JSON-serializable") from exc
        if size_bytes > DEFAULT_MAX_REFLEX_DECISION_BYTES:
            raise ValueError(
                "reflex_decision is too large "
                f"({size_bytes} bytes); max is {DEFAULT_MAX_REFLEX_DECISION_BYTES}"
            )
        return value

    @field_validator("plan")
    @classmethod
    def _validate_plan(cls, value: dict[str, object]) -> dict[str, object]:
        try:
            size_bytes = _json_bytes(value)
        except TypeError as exc:
            raise ValueError("plan must be JSON-serializable") from exc
        if size_bytes > DEFAULT_MAX_PLAN_BYTES:
            raise ValueError(
                f"plan is too large ({size_bytes} bytes); max is {DEFAULT_MAX_PLAN_BYTES}"
            )
        return value

    @field_validator("tool_results")
    @classmethod
    def _validate_tool_results(cls, value: list[dict[str, object]]) -> list[dict[str, object]]:
        total_bytes = 0
        for item in value:
            try:
                size_bytes = _json_bytes(item)
            except TypeError as exc:
                raise ValueError("tool_results must be JSON-serializable") from exc
            if size_bytes > DEFAULT_MAX_TOOL_RESULT_BYTES:
                raise ValueError(
                    "tool_results entry is too large "
                    f"({size_bytes} bytes); max is {DEFAULT_MAX_TOOL_RESULT_BYTES}"
                )
            total_bytes += size_bytes

        return value

    @field_validator("policy_decisions")
    @classmethod
    def _validate_policy_decisions(cls, value: list[dict[str, object]]) -> list[dict[str, object]]:
        for item in value:
            try:
                size_bytes = _json_bytes(item)
            except TypeError as exc:
                raise ValueError("policy_decisions must be JSON-serializable") from exc
            if size_bytes > DEFAULT_MAX_POLICY_DECISION_BYTES:
                raise ValueError(
                    "policy_decisions entry is too large "
                    f"({size_bytes} bytes); max is {DEFAULT_MAX_POLICY_DECISION_BYTES}"
                )
        return value

    @field_validator("tasks")
    @classmethod
    def _validate_tasks(cls, value: list[Task]) -> list[Task]:
        if len(value) > DEFAULT_MAX_TASKS:
            raise ValueError(f"too many tasks ({len(value)}); max is {DEFAULT_MAX_TASKS}")
        return value

    @model_validator(mode="after")
    def _validate_task_run_ids(self) -> RunPacket:
        mismatched = [task.task_id for task in self.tasks if task.run_id != self.run_id]
        if mismatched:
            raise ValueError(
                f"tasks must all share run_id={self.run_id}; mismatched task_ids={mismatched}"
            )
        return self

    @model_validator(mode="after")
    def _validate_timestamps(self) -> RunPacket:
        if self.started_at_ms is not None and self.started_at_ms < self.created_at_ms:
            raise ValueError("started_at_ms must be >= created_at_ms")
        if self.completed_at_ms is not None:
            if self.started_at_ms is not None and self.completed_at_ms < self.started_at_ms:
                raise ValueError("completed_at_ms must be >= started_at_ms")
            if self.completed_at_ms < self.created_at_ms:
                raise ValueError("completed_at_ms must be >= created_at_ms")
        return self

    @model_validator(mode="after")
    def _validate_total_size(self) -> RunPacket:
        try:
            size_bytes = _json_bytes(self.model_dump(mode="json"))
        except TypeError as exc:  # pragma: no cover
            raise ValueError("run packet must be JSON-serializable") from exc

        if size_bytes > DEFAULT_MAX_PACKET_BYTES:
            raise ValueError(
                f"run packet is too large ({size_bytes} bytes); max is {DEFAULT_MAX_PACKET_BYTES}"
            )
        return self

    def with_task_added(self, task: Task) -> RunPacket:
        updated = self.model_dump()
        updated["tasks"] = [*self.tasks, task]
        return RunPacket.model_validate(updated)

    def with_task_upserted(self, task: Task) -> RunPacket:
        updated = self.model_dump()
        replaced = False
        tasks: list[Task] = []
        for existing in self.tasks:
            if existing.task_id == task.task_id:
                tasks.append(task)
                replaced = True
                continue
            tasks.append(existing)
        if not replaced:
            tasks.append(task)
        updated["tasks"] = tasks
        return RunPacket.model_validate(updated)

    def with_tool_result_added(self, tool_result: dict[str, object]) -> RunPacket:
        updated = self.model_dump()
        updated["tool_results"] = [*self.tool_results, tool_result]
        return RunPacket.model_validate(updated)

    def with_policy_decision_added(self, policy_decision: dict[str, object]) -> RunPacket:
        updated = self.model_dump()
        updated["policy_decisions"] = [*self.policy_decisions, policy_decision]
        return RunPacket.model_validate(updated)

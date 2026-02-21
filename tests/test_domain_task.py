from __future__ import annotations

import json
import uuid

import pytest

from reflexor.domain.enums import TaskStatus, ToolCallStatus
from reflexor.domain.models import Task, ToolCall


def test_task_defaults_and_round_trip() -> None:
    task = Task(run_id=str(uuid.uuid4()), name="  step  ", created_at_ms=0)

    parsed = uuid.UUID(task.task_id)
    assert parsed.version == 4
    assert task.status == TaskStatus.PENDING
    assert task.name == "step"
    assert task.attempts == 0
    assert task.max_attempts == 1
    assert task.timeout_s == 60
    assert task.depends_on == []
    assert task.tool_call is None
    assert task.labels == []
    assert task.metadata == {}

    dumped = task.model_dump()
    restored = Task.model_validate(dumped)
    assert restored.model_dump() == dumped

    as_json = json.loads(task.model_dump_json())
    assert as_json["status"] == "pending"


def test_task_rejects_empty_name() -> None:
    with pytest.raises(ValueError, match="name must be non-empty"):
        Task(run_id=str(uuid.uuid4()), name=" ")


def test_task_validates_attempts_and_timeout() -> None:
    with pytest.raises(ValueError, match="attempts must be <= max_attempts"):
        Task(run_id=str(uuid.uuid4()), name="x", attempts=2, max_attempts=1)

    with pytest.raises(ValueError, match="timeout_s must be > 0"):
        Task(run_id=str(uuid.uuid4()), name="x", timeout_s=0)


def test_task_validates_timestamps() -> None:
    with pytest.raises(ValueError, match="started_at_ms must be >= created_at_ms"):
        Task(run_id=str(uuid.uuid4()), name="x", created_at_ms=10, started_at_ms=9)


def test_task_supports_optional_tool_call() -> None:
    tool_call = ToolCall(
        tool_name="example",
        permission_scope="tests",
        idempotency_key="k1",
        status=ToolCallStatus.SUCCEEDED,
        created_at_ms=0,
    )
    task = Task(
        run_id=str(uuid.uuid4()),
        name="uses tool",
        tool_call=tool_call,
        status=TaskStatus.RUNNING,
        created_at_ms=0,
    )

    dumped = task.model_dump()
    assert dumped["tool_call"]["status"] == "succeeded"


def test_task_rejects_non_json_metadata() -> None:
    with pytest.raises(ValueError, match="metadata must be JSON-serializable"):
        Task(run_id=str(uuid.uuid4()), name="x", metadata={"bad": object()})

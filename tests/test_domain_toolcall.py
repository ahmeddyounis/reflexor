from __future__ import annotations

import json
import uuid

import pytest

from reflexor.domain.enums import ToolCallStatus
from reflexor.domain.models import ToolCall


def test_tool_call_round_trip_serialization() -> None:
    tool_call = ToolCall(
        tool_name="  filesystem.read  ",
        args={"path": "/tmp/file.txt"},
        permission_scope="  fs.read  ",
        idempotency_key="  k1  ",
        status=ToolCallStatus.PENDING,
        created_at_ms=10,
        started_at_ms=20,
        completed_at_ms=30,
        result_ref="  result:1  ",
    )

    parsed = uuid.UUID(tool_call.tool_call_id)
    assert parsed.version == 4
    assert tool_call.tool_name == "filesystem.read"
    assert tool_call.permission_scope == "fs.read"
    assert tool_call.idempotency_key == "k1"
    assert tool_call.result_ref == "result:1"

    dumped = tool_call.model_dump()
    restored = ToolCall.model_validate(dumped)
    assert restored.model_dump() == dumped

    as_json = json.loads(tool_call.model_dump_json())
    assert as_json["status"] == "pending"


def test_tool_call_rejects_empty_fields() -> None:
    with pytest.raises(ValueError, match="tool_name must be non-empty"):
        ToolCall(tool_name=" ", permission_scope="x", idempotency_key="k")

    with pytest.raises(ValueError, match="permission_scope must be non-empty"):
        ToolCall(tool_name="x", permission_scope=" ", idempotency_key="k")

    with pytest.raises(ValueError, match="idempotency_key must be non-empty"):
        ToolCall(tool_name="x", permission_scope="x", idempotency_key=" ")


def test_tool_call_rejects_non_json_args() -> None:
    with pytest.raises(ValueError, match="args must be valid JSON"):
        ToolCall(
            tool_name="x",
            permission_scope="x",
            idempotency_key="k",
            args={"bad": object()},
        )


def test_tool_call_rejects_invalid_timestamps() -> None:
    with pytest.raises(ValueError, match="started_at_ms must be >= created_at_ms"):
        ToolCall(
            tool_name="x",
            permission_scope="x",
            idempotency_key="k",
            created_at_ms=10,
            started_at_ms=9,
        )

    with pytest.raises(ValueError, match="completed_at_ms must be >= started_at_ms"):
        ToolCall(
            tool_name="x",
            permission_scope="x",
            idempotency_key="k",
            created_at_ms=10,
            started_at_ms=20,
            completed_at_ms=19,
        )


def test_tool_call_rejects_non_uuid4_id() -> None:
    with pytest.raises(ValueError, match="tool_call_id must be a UUID4"):
        ToolCall(
            tool_call_id=str(uuid.uuid1()),
            tool_name="x",
            permission_scope="x",
            idempotency_key="k",
        )

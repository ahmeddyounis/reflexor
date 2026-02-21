from __future__ import annotations

import json

from reflexor.domain.enums import ApprovalStatus, RunStatus, TaskStatus, ToolCallStatus
from reflexor.domain.models import Approval, Task, ToolCall


def test_enum_values_are_stable() -> None:
    assert [status.value for status in TaskStatus] == [
        "pending",
        "running",
        "succeeded",
        "failed",
        "canceled",
    ]
    assert [status.value for status in ToolCallStatus] == [
        "pending",
        "running",
        "succeeded",
        "failed",
        "denied",
        "canceled",
    ]
    assert [status.value for status in ApprovalStatus] == [
        "pending",
        "approved",
        "denied",
        "expired",
        "canceled",
    ]
    assert [status.value for status in RunStatus] == [
        "created",
        "running",
        "succeeded",
        "failed",
        "canceled",
    ]


def test_pydantic_serialization_uses_string_values() -> None:
    task = Task(title="t1", status=TaskStatus.RUNNING)
    tool_call = ToolCall(tool_name="example", status="denied")
    approval = Approval(status=ApprovalStatus.APPROVED)

    task_json = json.loads(task.model_dump_json())
    tool_call_json = json.loads(tool_call.model_dump_json())
    approval_json = json.loads(approval.model_dump_json())

    assert task_json["status"] == "running"
    assert tool_call_json["status"] == "denied"
    assert approval_json["status"] == "approved"

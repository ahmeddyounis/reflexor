from __future__ import annotations

import json
import uuid

from reflexor.domain.enums import ApprovalStatus, RunStatus, TaskStatus, ToolCallStatus
from reflexor.domain.models import Approval, Task, ToolCall


def test_enum_values_are_stable() -> None:
    assert [status.value for status in TaskStatus] == [
        "pending",
        "queued",
        "waiting_approval",
        "running",
        "succeeded",
        "failed",
        "canceled",
        "archived",
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
        "archived",
    ]


def test_pydantic_serialization_uses_string_values() -> None:
    run_id = str(uuid.uuid4())
    task = Task(
        run_id=run_id,
        name="t1",
        status=TaskStatus.RUNNING,
        created_at_ms=0,
        timeout_s=1,
    )
    tool_call = ToolCall(
        tool_name="example",
        permission_scope="tests",
        idempotency_key="idempotency-1",
        status="denied",
        created_at_ms=0,
    )
    approval = Approval(
        run_id=run_id,
        task_id=task.task_id,
        tool_call_id=tool_call.tool_call_id,
        status=ApprovalStatus.APPROVED,
        created_at_ms=0,
        decided_at_ms=1,
    )

    task_json = json.loads(task.model_dump_json())
    tool_call_json = json.loads(tool_call.model_dump_json())
    approval_json = json.loads(approval.model_dump_json())

    assert task_json["status"] == "running"
    assert tool_call_json["status"] == "denied"
    assert approval_json["status"] == "approved"

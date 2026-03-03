from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from reflexor.config import ReflexorSettings
from reflexor.domain.enums import ApprovalStatus
from reflexor.domain.models import Approval, ToolCall
from reflexor.security.policy.approvals import ApprovalBuilder, InMemoryApprovalStore
from reflexor.security.policy.context import tool_spec_from_tool
from reflexor.security.policy.decision import PolicyDecision
from reflexor.tools.mock_tool import MockTool


def _tool_call(*, tool_call_id: str) -> ToolCall:
    secret = "sk-abcdefghijklmnopqrstuvwxyz0123456789"
    return ToolCall(
        tool_call_id=tool_call_id,
        tool_name="tests.mock",
        permission_scope="fs.read",
        idempotency_key="k",
        args={
            "url": f"https://example.com/path?token={secret}",
            "headers": {"Authorization": "Bearer abcdefghijklmnopqrstuvwxyz"},
            "body": f"hello {secret}",
        },
    )


@pytest.mark.asyncio
async def test_in_memory_store_create_pending_is_idempotent_by_tool_call_id(tmp_path: Path) -> None:
    store = InMemoryApprovalStore()
    tool_call_id = str(uuid4())

    approval_1 = Approval(
        run_id=str(uuid4()),
        task_id=str(uuid4()),
        tool_call_id=tool_call_id,
        created_at_ms=0,
        preview="p1",
    )
    approval_2 = Approval(
        run_id=str(uuid4()),
        task_id=str(uuid4()),
        tool_call_id=tool_call_id,
        created_at_ms=1,
        preview="p2",
    )

    created_1 = await store.create_pending(approval_1)
    created_2 = await store.create_pending(approval_2)

    assert created_2.approval_id == created_1.approval_id
    assert (await store.list_pending(limit=10, offset=0))[0].approval_id == created_1.approval_id


@pytest.mark.asyncio
async def test_store_decide_updates_status_and_hides_from_pending(tmp_path: Path) -> None:
    store = InMemoryApprovalStore()
    tool_call_id = str(uuid4())
    created = await store.create_pending(
        Approval(
            run_id=str(uuid4()),
            task_id=str(uuid4()),
            tool_call_id=tool_call_id,
            created_at_ms=0,
        )
    )

    approved = await store.decide(created.approval_id, ApprovalStatus.APPROVED, decided_by="alice")
    assert approved.status == ApprovalStatus.APPROVED
    assert approved.decided_by == "alice"
    assert approved.decided_at_ms is not None

    pending = await store.list_pending(limit=10, offset=0)
    assert pending == []


def test_approval_builder_preview_and_hash_never_include_secret_values(tmp_path: Path) -> None:
    settings = ReflexorSettings(workspace_root=tmp_path)
    builder = ApprovalBuilder(settings=settings, max_preview_bytes=400)

    tool = MockTool(tool_name="tests.mock", permission_scope="fs.read")
    tool_spec = tool_spec_from_tool(tool)
    tool_call = _tool_call(tool_call_id=str(uuid4()))
    parsed_args = tool.ArgsModel.model_validate(tool_call.args)
    decision = PolicyDecision.require_approval(rule_id="tests.rule")

    approval = builder.build_pending(
        run_id=str(uuid4()),
        task_id=str(uuid4()),
        tool_call=tool_call,
        tool_spec=tool_spec,
        parsed_args=parsed_args,
        decision=decision,
    )

    assert approval.preview is not None
    assert "sk-abcdefghijklmnopqrstuvwxyz0123456789" not in approval.preview
    assert "abcdefghijklmnopqrstuvwxyz" not in approval.preview
    assert "token=" not in approval.preview
    assert "hello " not in approval.preview
    assert "https://example.com/path" in approval.preview
    assert len(approval.preview.encode("utf-8")) <= builder.max_preview_bytes

    payload_hash, hash_input = builder.build_payload_hash_for_args(args=tool_call.args)
    assert approval.payload_hash == payload_hash
    assert "sk-abcdefghijklmnopqrstuvwxyz0123456789" not in hash_input
    assert "abcdefghijklmnopqrstuvwxyz" not in hash_input
    json.loads(hash_input)

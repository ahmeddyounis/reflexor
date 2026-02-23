from __future__ import annotations

from uuid import uuid4

from pydantic import BaseModel

from reflexor.domain.models import ToolCall
from reflexor.security.policy.approvals import ApprovalBuilder
from reflexor.security.policy.context import ToolSpec
from reflexor.security.policy.decision import PolicyDecision
from reflexor.tools.sdk import ToolManifest


class HttpArgs(BaseModel):
    url: str
    headers: dict[str, str]
    body: str


def _tool_spec(*, manifest: ToolManifest) -> ToolSpec:
    return ToolSpec(tool_name=manifest.name, manifest=manifest, args_model=HttpArgs)


def test_payload_hash_is_stable_across_dict_key_order() -> None:
    builder = ApprovalBuilder()

    args_a = {"url": "https://example.com/x", "token": "secret", "n": 1}
    args_b = {"n": 1, "token": "secret", "url": "https://example.com/x"}

    hash_a, input_a = builder.build_payload_hash_for_args(args=args_a)
    hash_b, input_b = builder.build_payload_hash_for_args(args=args_b)

    assert input_a == input_b
    assert hash_a == hash_b


def test_payload_hash_input_is_redacted_and_idempotent_over_secrets() -> None:
    builder = ApprovalBuilder()

    args_1 = {
        "headers": {"Authorization": "Bearer SUPERSECRET12345"},
        "token": "top-secret",
        "url": "https://example.com/api",
    }
    args_2 = {
        "headers": {"Authorization": "Bearer DIFFERENTSECRET67890"},
        "token": "another-secret",
        "url": "https://example.com/api",
    }

    hash_1, input_1 = builder.build_payload_hash_for_args(args=args_1)
    hash_2, input_2 = builder.build_payload_hash_for_args(args=args_2)

    assert "SUPERSECRET12345" not in input_1
    assert "top-secret" not in input_1
    assert "DIFFERENTSECRET67890" not in input_2
    assert "another-secret" not in input_2

    assert hash_1 == hash_2
    assert input_1 == input_2


def test_preview_avoids_query_and_body_and_is_bounded() -> None:
    builder = ApprovalBuilder(max_preview_bytes=250)

    manifest = ToolManifest(
        name="tests.http",
        version="0.1.0",
        description="http",
        permission_scope="net.http",
        idempotent=True,
        side_effects=False,
    )
    tool_spec = _tool_spec(manifest=manifest)
    tool_call = ToolCall(
        tool_name=manifest.name,
        permission_scope=manifest.permission_scope,
        idempotency_key="k",
        args={
            "url": "https://example.com/path?token=SUPERSECRET12345",
            "headers": {"Authorization": "Bearer SUPERSECRET12345"},
            "body": "hello SUPERSECRET12345",
        },
    )
    parsed = HttpArgs(
        url="https://example.com/path?token=SUPERSECRET12345",
        headers={"Authorization": "Bearer SUPERSECRET12345"},
        body="hello SUPERSECRET12345",
    )
    decision = PolicyDecision.require_approval(rule_id="tests.rule")

    preview = builder.build_preview(
        tool_call=tool_call, tool_spec=tool_spec, parsed_args=parsed, decision=decision
    )

    assert "SUPERSECRET12345" not in preview
    assert "token=SUPERSECRET12345" not in preview
    assert "hello " not in preview
    assert "https://example.com/path" in preview
    assert "<truncated>" in preview
    assert len(preview.encode("utf-8")) <= builder.max_preview_bytes


def test_build_pending_creates_approval_with_hash_and_preview() -> None:
    builder = ApprovalBuilder()

    manifest = ToolManifest(
        name="tests.http",
        version="0.1.0",
        description="http",
        permission_scope="net.http",
        idempotent=True,
    )
    tool_spec = _tool_spec(manifest=manifest)
    tool_call = ToolCall(
        tool_name=manifest.name,
        permission_scope=manifest.permission_scope,
        idempotency_key="k",
        args={
            "url": "https://example.com/path",
            "headers": {"Authorization": "Bearer SUPERSECRET12345"},
            "body": "hello",
        },
    )
    parsed = HttpArgs(
        url="https://example.com/path",
        headers={"Authorization": "Bearer SUPERSECRET12345"},
        body="hello",
    )
    decision = PolicyDecision.require_approval(rule_id="tests.rule")

    approval = builder.build_pending(
        run_id=str(uuid4()),
        task_id=str(uuid4()),
        tool_call=tool_call,
        tool_spec=tool_spec,
        parsed_args=parsed,
        decision=decision,
    )

    assert approval.tool_call_id == tool_call.tool_call_id
    assert approval.payload_hash is not None
    assert approval.preview is not None

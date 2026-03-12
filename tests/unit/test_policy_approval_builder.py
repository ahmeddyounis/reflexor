from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel

from reflexor.config import ReflexorSettings
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

    args_1: dict[str, object] = {
        "headers": {"Authorization": "Bearer SUPERSECRET12345"},
        "token": "top-secret",
        "url": "https://example.com/api",
    }
    args_2: dict[str, object] = {
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


def test_payload_hash_uses_full_redacted_args_even_with_small_size_limits(tmp_path: Path) -> None:
    settings = ReflexorSettings(
        workspace_root=tmp_path,
        max_event_payload_bytes=80,
        max_tool_output_bytes=80,
        max_run_packet_bytes=80,
    )
    builder = ApprovalBuilder(settings=settings)

    args_a: dict[str, object] = {"body": ("A" * 200) + "X"}
    args_b: dict[str, object] = {"body": ("A" * 200) + "Y"}

    hash_a, input_a = builder.build_payload_hash_for_args(args=args_a)
    hash_b, input_b = builder.build_payload_hash_for_args(args=args_b)

    assert hash_a != hash_b
    assert input_a != input_b
    assert "<truncated>" not in input_a
    assert "<truncated>" not in input_b


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


def test_preview_never_includes_raw_authorization_header_values() -> None:
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
            "headers": {
                "Authorization": "Bearer SUPERSECRET12345",
                "X-Api-Key": "sk-super-secret-token",
            },
        },
    )
    parsed = HttpArgs(
        url="https://example.com/path",
        headers={
            "Authorization": "Bearer SUPERSECRET12345",
            "X-Api-Key": "sk-super-secret-token",
        },
        body="",
    )
    decision = PolicyDecision.require_approval(rule_id="tests.rule")

    preview = builder.build_preview(
        tool_call=tool_call, tool_spec=tool_spec, parsed_args=parsed, decision=decision
    )

    assert "Bearer" not in preview
    assert "SUPERSECRET12345" not in preview
    assert "sk-super-secret-token" not in preview


def test_preview_strips_query_and_fragment_from_malformed_url() -> None:
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
        args={"url": "not a url?token=SUPERSECRET12345#frag", "headers": {}, "body": ""},
    )
    parsed = HttpArgs(url="not a url?token=SUPERSECRET12345#frag", headers={}, body="")
    decision = PolicyDecision.require_approval(rule_id="tests.rule")

    preview = builder.build_preview(
        tool_call=tool_call, tool_spec=tool_spec, parsed_args=parsed, decision=decision
    )

    assert "token=SUPERSECRET12345" not in preview
    assert "#frag" not in preview
    assert "url: not a url" in preview

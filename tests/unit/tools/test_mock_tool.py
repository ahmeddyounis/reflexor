from __future__ import annotations

import asyncio
import time
from pathlib import Path

from reflexor.domain.serialization import canonical_json, stable_sha256
from reflexor.tools.mock_tool import MockPlan, args_hash_for, call_key_for
from reflexor.tools.sdk.tool import ToolContext


def test_records_invocations_and_deterministic_key(register_mock_tool, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    tool = register_mock_tool("tests.mock")

    ctx = ToolContext(
        workspace_root=tmp_path,
        dry_run=True,
        timeout_s=1.0,
        correlation_ids={
            "event_id": "evt",
            "run_id": "run",
            "task_id": "task",
            "tool_call_id": "tool_call",
        },
    )
    args = tool.ArgsModel.model_validate({"x": 1, "y": "z"})

    before_ms = int(time.time() * 1000)
    result = asyncio.run(tool.run(args, ctx))
    after_ms = int(time.time() * 1000)

    assert result.ok is True
    assert isinstance(result.data, dict)

    expected_args = {"x": 1, "y": "z"}
    expected_args_hash = stable_sha256(canonical_json(expected_args))
    expected_key = call_key_for(tool_name="tests.mock", args_hash=expected_args_hash)

    assert result.data["call_key"] == expected_key
    assert result.data["args_hash"] == expected_args_hash

    assert len(tool.invocations) == 1
    invocation = tool.invocations[0]
    assert invocation.call_key == expected_key
    assert invocation.args_hash == expected_args_hash
    assert invocation.args == expected_args
    assert invocation.dry_run is True
    assert invocation.correlation_ids["run_id"] == "run"
    assert before_ms <= invocation.called_at_ms <= after_ms


def test_default_response_is_stable_across_calls(register_mock_tool, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    tool = register_mock_tool("tests.mock")
    ctx = ToolContext(workspace_root=tmp_path, dry_run=False, timeout_s=1.0)
    args = tool.ArgsModel.model_validate({"x": 1})

    first = asyncio.run(tool.run(args, ctx))
    second = asyncio.run(tool.run(args, ctx))

    assert first.ok is True
    assert second.ok is True
    assert first.data == second.data


def test_transient_failure_plan_then_success(register_mock_tool, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    tool = register_mock_tool("tests.mock")
    ctx = ToolContext(workspace_root=tmp_path, dry_run=False, timeout_s=1.0)
    args_payload = {"x": 1}
    args = tool.ArgsModel.model_validate(args_payload)

    tool.set_transient_failures_then_success(args_payload, failures=2)

    first = asyncio.run(tool.run(args, ctx))
    second = asyncio.run(tool.run(args, ctx))
    third = asyncio.run(tool.run(args, ctx))

    assert first.ok is False
    assert first.error_code == "TOOL_ERROR"
    assert second.ok is False
    assert third.ok is True


def test_permanent_failure_plan(register_mock_tool, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    tool = register_mock_tool("tests.mock")
    ctx = ToolContext(workspace_root=tmp_path, dry_run=False, timeout_s=1.0)
    args_payload = {"x": 1}
    args = tool.ArgsModel.model_validate(args_payload)

    tool.set_permanent_failure(args_payload, error_code="PERM", error_message="nope")

    first = asyncio.run(tool.run(args, ctx))
    second = asyncio.run(tool.run(args, ctx))

    assert first.ok is False
    assert first.error_code == "PERM"
    assert second.ok is False


def test_set_plan_by_key_allows_precise_control(register_mock_tool, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    tool = register_mock_tool("tests.mock")
    ctx = ToolContext(workspace_root=tmp_path, dry_run=False, timeout_s=1.0)

    args_payload = {"x": 1}
    key = tool.key_for_args(args_payload)
    tool.set_plan(key=key, plan=MockPlan.permanent_failure(error_code="X", error_message="boom"))

    args = tool.ArgsModel.model_validate(args_payload)
    result = asyncio.run(tool.run(args, ctx))

    assert result.ok is False
    assert result.error_code == "X"


def test_args_hash_helpers_are_deterministic() -> None:
    payload1 = {"b": 2, "a": 1}
    payload2 = {"a": 1, "b": 2}
    assert args_hash_for(payload1) == args_hash_for(payload2)

    key1 = call_key_for(tool_name="tests.mock", args_hash=args_hash_for(payload1))
    key2 = call_key_for(tool_name="tests.mock", args_hash=args_hash_for(payload2))
    assert key1 == key2

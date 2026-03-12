from __future__ import annotations

import math

import pytest

from reflexor.tools.sdk import ToolManifest, ToolResult


def test_tool_manifest_rejects_blank_name() -> None:
    with pytest.raises(ValueError, match="name must be non-empty"):
        ToolManifest(
            name="  ",
            version="0.1.0",
            description="test",
            permission_scope="fs.read",
        )


def test_tool_manifest_rejects_blank_permission_scope() -> None:
    with pytest.raises(ValueError, match="permission_scope must be non-empty"):
        ToolManifest(
            name="debug.echo",
            version="0.1.0",
            description="test",
            permission_scope=" ",
        )


def test_tool_manifest_normalizes_fields_and_round_trips() -> None:
    manifest = ToolManifest(
        name=" debug.echo ",
        version=" 0.1.0 ",
        description=" Echo args. ",
        permission_scope=" fs.read ",
        default_timeout_s=5,
        max_output_bytes=1024,
        tags=[" debug ", "debug", "tools"],
    )

    assert manifest.name == "debug.echo"
    assert manifest.version == "0.1.0"
    assert manifest.description == "Echo args."
    assert manifest.permission_scope == "fs.read"
    assert manifest.tags == ["debug", "tools"]

    payload = manifest.model_dump()
    assert ToolManifest.model_validate(payload) == manifest


def test_tool_manifest_rejects_non_positive_limits() -> None:
    with pytest.raises(ValueError, match="default_timeout_s must be > 0"):
        ToolManifest(
            name="debug.echo",
            version="0.1.0",
            description="test",
            permission_scope="fs.read",
            default_timeout_s=0,
        )

    with pytest.raises(ValueError, match="max_output_bytes must be > 0"):
        ToolManifest(
            name="debug.echo",
            version="0.1.0",
            description="test",
            permission_scope="fs.read",
            max_output_bytes=-1,
        )


def test_tool_result_round_trip_ok() -> None:
    result = ToolResult(ok=True, data={"answer": 42}, debug={"trace_id": "abc"})
    payload = result.model_dump()
    assert ToolResult.model_validate(payload) == result


def test_tool_result_enforces_ok_error_invariants() -> None:
    with pytest.raises(ValueError, match="ok=true must not include error_code/error_message"):
        ToolResult(ok=True, data={"x": 1}, error_message="nope")

    with pytest.raises(ValueError, match="ok=false requires error_message"):
        ToolResult(ok=False, error_code="E_TOOL")


def test_tool_result_rejects_non_json_serializable_fields() -> None:
    with pytest.raises(ValueError, match="data must be JSON-serializable"):
        ToolResult(ok=True, data={"bad": object()})

    with pytest.raises(ValueError, match="debug must be JSON-serializable"):
        ToolResult(ok=True, debug={"bad": object()})

    with pytest.raises(ValueError, match="produced_artifacts must be JSON-serializable"):
        ToolResult(ok=True, produced_artifacts=[{"bad": object()}])

    with pytest.raises(ValueError, match="data must be JSON-serializable"):
        ToolResult(ok=True, data={"delay_s": math.inf})

    with pytest.raises(ValueError, match="output_schema must be JSON-serializable"):
        ToolManifest(
            name="debug.echo",
            version="0.1.0",
            description="test",
            permission_scope="fs.read",
            output_schema={"value": math.nan},
        )


def test_tool_result_strips_and_validates_error_fields() -> None:
    result = ToolResult(ok=False, error_code=" E_TOOL ", error_message=" oops ")
    assert result.error_code == "E_TOOL"
    assert result.error_message == "oops"

    with pytest.raises(ValueError, match="error_code must be non-empty when provided"):
        ToolResult(ok=False, error_code="  ", error_message="oops")

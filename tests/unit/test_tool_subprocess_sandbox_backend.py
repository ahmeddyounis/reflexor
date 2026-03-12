from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic import ValidationError
from tests.fixtures.sandbox_registry import build_registry

import reflexor.tools.execution_backend.subprocess as subprocess_backend_module
from reflexor.config import ReflexorSettings
from reflexor.tools import sandbox_runner
from reflexor.tools.execution_backend import SubprocessSandboxBackend
from reflexor.tools.execution_backend.protocol import _SandboxToolContext
from reflexor.tools.mock_tool import args_hash_for, call_key_for
from reflexor.tools.runner import ToolRunner
from reflexor.tools.sdk import ToolContext


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_subprocess_sandbox_backend_runs_mock_tool(tmp_path: Path) -> None:
    settings = ReflexorSettings(workspace_root=tmp_path, max_tool_output_bytes=50_000)
    registry = build_registry(settings)
    backend = SubprocessSandboxBackend(
        registry_factory="tests.fixtures.sandbox_registry:build_registry",
        env_allowlist=["PYTHONPATH"],
        extra_env={"PYTHONPATH": str(_repo_root())},
    )
    runner = ToolRunner(registry=registry, settings=settings, backend=backend)

    ctx = ToolContext(workspace_root=tmp_path, timeout_s=2.0)
    result = await runner.run_tool("tests.mock", {"x": 1}, ctx=ctx)

    assert result.ok is True
    assert isinstance(result.data, dict)
    expected_hash = args_hash_for({"x": 1})
    expected_key = call_key_for(tool_name="tests.mock", args_hash=expected_hash)
    assert result.data["tool_name"] == "tests.mock"
    assert result.data["args_hash"] == expected_hash
    assert result.data["call_key"] == expected_key


@pytest.mark.asyncio
async def test_subprocess_sandbox_backend_strips_env_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TOP_SECRET", "shh")

    settings = ReflexorSettings(workspace_root=tmp_path, max_tool_output_bytes=50_000)
    registry = build_registry(settings)
    backend = SubprocessSandboxBackend(
        registry_factory="tests.fixtures.sandbox_registry:build_registry",
        env_allowlist=["PYTHONPATH"],
        extra_env={"PYTHONPATH": str(_repo_root())},
    )
    runner = ToolRunner(registry=registry, settings=settings, backend=backend)

    ctx = ToolContext(workspace_root=tmp_path, timeout_s=2.0)
    result = await runner.run_tool("tests.env_probe", {"name": "TOP_SECRET"}, ctx=ctx)

    assert result.ok is True
    assert isinstance(result.data, dict)
    assert result.data["value"] is None


@pytest.mark.asyncio
async def test_subprocess_sandbox_backend_allows_allowlisted_env_vars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TOP_SECRET", "shh")

    settings = ReflexorSettings(workspace_root=tmp_path, max_tool_output_bytes=50_000)
    registry = build_registry(settings)
    backend = SubprocessSandboxBackend(
        registry_factory="tests.fixtures.sandbox_registry:build_registry",
        env_allowlist=["PYTHONPATH", "TOP_SECRET"],
        extra_env={"PYTHONPATH": str(_repo_root())},
    )
    runner = ToolRunner(registry=registry, settings=settings, backend=backend)

    ctx = ToolContext(workspace_root=tmp_path, timeout_s=2.0)
    result = await runner.run_tool("tests.env_probe", {"name": "TOP_SECRET"}, ctx=ctx)

    assert result.ok is True
    assert isinstance(result.data, dict)
    assert result.data["value"] == "shh"


def test_subprocess_sandbox_backend_enforces_timeout(tmp_path: Path) -> None:
    settings = ReflexorSettings(workspace_root=tmp_path, max_tool_output_bytes=50_000)
    registry = build_registry(settings)
    backend = SubprocessSandboxBackend(
        registry_factory="tests.fixtures.sandbox_registry:build_registry",
        env_allowlist=["PYTHONPATH"],
        extra_env={"PYTHONPATH": str(_repo_root())},
    )
    runner = ToolRunner(registry=registry, settings=settings, backend=backend)

    ctx = ToolContext(workspace_root=tmp_path, timeout_s=0.5)
    result = asyncio.run(runner.run_tool("tests.sleep", {"seconds": 5.0}, ctx=ctx))

    assert result.ok is False
    assert result.error_code == "TIMEOUT"
    assert result.error_message is not None


def test_subprocess_sandbox_backend_rejects_blank_module() -> None:
    with pytest.raises(ValueError, match="module must be non-empty"):
        SubprocessSandboxBackend(module="   ")


def test_subprocess_sandbox_backend_resolves_python_executable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(subprocess_backend_module.shutil, "which", lambda value: "/tmp/python3")

    backend = SubprocessSandboxBackend(python_executable="python3")

    assert backend.python_executable == "/tmp/python3"


def test_sandbox_tool_context_requires_absolute_workspace_root() -> None:
    with pytest.raises(ValidationError, match="workspace_root must be an absolute path"):
        _SandboxToolContext(workspace_root="relative/path", timeout_s=1.0)


def test_sandbox_runner_reports_unexpected_internal_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: dict[str, object] = {}

    async def _boom(request: sandbox_runner.SandboxRequest) -> object:
        _ = request
        raise RuntimeError("boom")

    def _record_protocol_error(
        *, message: str, debug: dict[str, object] | None = None
    ) -> None:
        calls["message"] = message
        calls["debug"] = debug

    monkeypatch.setattr(
        sandbox_runner,
        "_read_stdin_json",
        lambda: {
            "protocol_version": 1,
            "tool_name": "tests.mock",
            "args": {},
            "ctx": {"workspace_root": str(tmp_path), "timeout_s": 1.0},
            "settings": {},
        },
    )
    monkeypatch.setattr(sandbox_runner, "_run_request", _boom)
    monkeypatch.setattr(sandbox_runner, "_protocol_error", _record_protocol_error)

    asyncio.run(sandbox_runner.main())

    assert calls["message"] == "sandbox execution failed"
    debug = calls["debug"]
    assert isinstance(debug, dict)
    assert "RuntimeError" in str(debug["exception"])

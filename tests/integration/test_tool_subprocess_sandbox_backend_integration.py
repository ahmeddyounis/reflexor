from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from tests.fixtures.sandbox_registry import build_registry

from reflexor.config import ReflexorSettings
from reflexor.tools.execution_backend import SubprocessSandboxBackend
from reflexor.tools.fs_tool import FsReadTextTool
from reflexor.tools.registry import ToolRegistry
from reflexor.tools.runner import ToolRunner
from reflexor.tools.sdk import ToolContext


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_subprocess_sandbox_backend_runs_builtin_tools(tmp_path: Path) -> None:
    (tmp_path / "hello.txt").write_text("hello", encoding="utf-8")

    settings = ReflexorSettings(workspace_root=tmp_path, max_tool_output_bytes=50_000)
    registry = ToolRegistry()
    registry.register(FsReadTextTool(settings=settings))

    runner = ToolRunner(
        registry=registry,
        settings=settings,
        backend=SubprocessSandboxBackend(),
    )

    ctx = ToolContext(workspace_root=tmp_path, timeout_s=2.0)
    result = asyncio.run(runner.run_tool("fs.read_text", {"path": "hello.txt"}, ctx=ctx))

    assert result.ok is True
    assert isinstance(result.data, dict)
    assert result.data["text"] == "hello"


def test_subprocess_sandbox_backend_cannot_access_blocked_env_vars(
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
    runner = ToolRunner(
        registry=registry,
        settings=settings,
        backend=backend,
    )

    ctx = ToolContext(workspace_root=tmp_path, timeout_s=2.0)
    result = asyncio.run(runner.run_tool("tests.env_probe", {"name": "TOP_SECRET"}, ctx=ctx))

    assert result.ok is True
    assert isinstance(result.data, dict)
    assert result.data["name"] == "TOP_SECRET"
    assert result.data["value"] is None

from __future__ import annotations

from pathlib import Path

import pytest
from tests.fixtures.sandbox_registry import build_registry

from reflexor.config import ReflexorSettings
from reflexor.tools.runner import ToolRunner
from reflexor.tools.sandbox_policy import SandboxPolicy, SandboxPolicyBackend
from reflexor.tools.sdk import ToolContext


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_sandbox_policy_backend_disabled_runs_in_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TOP_SECRET", "shh")

    settings = ReflexorSettings(
        workspace_root=tmp_path,
        sandbox_enabled=False,
        sandbox_tools=["tests.env_probe"],
        sandbox_env_allowlist=["PYTHONPATH"],
        max_tool_output_bytes=50_000,
    )
    registry = build_registry(settings)
    policy = SandboxPolicy.from_settings(settings)
    backend = SandboxPolicyBackend(
        policy=policy,
        registry_factory="tests.fixtures.sandbox_registry:build_registry",
        extra_env={"PYTHONPATH": str(_repo_root())},
    )
    runner = ToolRunner(registry=registry, settings=settings, backend=backend)

    ctx = ToolContext(workspace_root=tmp_path, timeout_s=2.0)
    result = await runner.run_tool("tests.env_probe", {"name": "TOP_SECRET"}, ctx=ctx)

    assert result.ok is True
    assert isinstance(result.data, dict)
    assert result.data["value"] == "shh"


@pytest.mark.asyncio
async def test_sandbox_policy_backend_enabled_but_not_listed_runs_in_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TOP_SECRET", "shh")

    settings = ReflexorSettings(
        workspace_root=tmp_path,
        sandbox_enabled=True,
        sandbox_tools=["tests.mock"],
        sandbox_env_allowlist=["PYTHONPATH"],
        max_tool_output_bytes=50_000,
    )
    registry = build_registry(settings)
    policy = SandboxPolicy.from_settings(settings)
    backend = SandboxPolicyBackend(
        policy=policy,
        registry_factory="tests.fixtures.sandbox_registry:build_registry",
        extra_env={"PYTHONPATH": str(_repo_root())},
    )
    runner = ToolRunner(registry=registry, settings=settings, backend=backend)

    ctx = ToolContext(workspace_root=tmp_path, timeout_s=2.0)
    result = await runner.run_tool("tests.env_probe", {"name": "TOP_SECRET"}, ctx=ctx)

    assert result.ok is True
    assert isinstance(result.data, dict)
    assert result.data["value"] == "shh"


@pytest.mark.asyncio
async def test_sandbox_policy_backend_sandboxes_listed_tool_and_strips_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TOP_SECRET", "shh")

    settings = ReflexorSettings(
        workspace_root=tmp_path,
        sandbox_enabled=True,
        sandbox_tools=["tests.env_probe"],
        sandbox_env_allowlist=["PYTHONPATH"],
        max_tool_output_bytes=50_000,
    )
    registry = build_registry(settings)
    policy = SandboxPolicy.from_settings(settings)
    backend = SandboxPolicyBackend(
        policy=policy,
        registry_factory="tests.fixtures.sandbox_registry:build_registry",
        extra_env={"PYTHONPATH": str(_repo_root())},
    )
    runner = ToolRunner(registry=registry, settings=settings, backend=backend)

    ctx = ToolContext(workspace_root=tmp_path, timeout_s=2.0)
    result = await runner.run_tool("tests.env_probe", {"name": "TOP_SECRET"}, ctx=ctx)

    assert result.ok is True
    assert isinstance(result.data, dict)
    assert result.data["value"] is None


from __future__ import annotations

import importlib.metadata
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

import reflexor.tools.registry as registry_module
from reflexor.config import ReflexorSettings
from reflexor.security.scopes import Scope
from reflexor.tools.registry import ToolRegistry
from reflexor.tools.sdk import ToolContext, ToolManifest, ToolResult


class _FakeDist:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeEntryPoint:
    def __init__(self, *, name: str, obj: object, dist_name: str) -> None:
        self.name = name
        self._obj = obj
        self.dist = _FakeDist(dist_name)
        self.load_called = False

    def load(self) -> object:
        self.load_called = True
        return self._obj


class _LogSink:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def warning(self, event: str, **kwargs: object) -> None:
        self.events.append((event, dict(kwargs)))


class _PluginArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    message: str


class _PluginTool:
    manifest = ToolManifest(
        name="plugin.echo",
        version="0.1.0",
        description="Plugin tool for hardening integration test.",
        permission_scope=Scope.FS_READ.value,
        idempotent=True,
    )
    ArgsModel = _PluginArgs

    async def run(self, args: _PluginArgs, ctx: ToolContext) -> ToolResult:
        return ToolResult(ok=True, data={"message": args.message, "dry_run": bool(ctx.dry_run)})


def test_entrypoint_discovery_loads_allowed_and_blocks_denied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sink = _LogSink()
    monkeypatch.setattr(registry_module, "_logger", sink)

    allowed_ep = _FakeEntryPoint(name="allowed", obj=_PluginTool(), dist_name="trusted-pkg")
    blocked_ep = _FakeEntryPoint(name="blocked", obj=_PluginTool(), dist_name="bad-pkg")

    def _fake_entry_points(**params: object) -> object:
        assert params.get("group") == "reflexor.tools"
        return [allowed_ep, blocked_ep]

    monkeypatch.setattr(importlib.metadata, "entry_points", _fake_entry_points)

    settings = ReflexorSettings(
        workspace_root=tmp_path,
        profile="prod",
        enable_tool_entrypoints=True,
        trusted_tool_packages=["trusted-pkg"],
        blocked_tool_packages=["bad-pkg"],
    )
    registry = ToolRegistry()

    assert registry.load_entrypoints(settings=settings) == 1
    assert allowed_ep.load_called is True
    assert blocked_ep.load_called is False
    registry.validate_exists("plugin.echo")

    refused = [event for event in sink.events if event[0] == "tool_entrypoint_refused"]
    assert refused, "expected a refusal log event for the blocked package"
    assert any(item[1].get("reason") == "blocked_package" for item in refused)

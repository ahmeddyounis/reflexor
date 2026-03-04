from __future__ import annotations

import importlib.metadata
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from reflexor.config import ReflexorSettings
from reflexor.security.scopes import Scope
from reflexor.tools.registry import ToolRegistry
from reflexor.tools.sdk import TOOL_SDK_VERSION, ToolContext, ToolManifest, ToolResult


class FakeEntryPoint:
    def __init__(self, *, name: str, obj: object) -> None:
        self.name = name
        self._obj = obj

    def load(self) -> object:
        return self._obj


class PluginArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    message: str


class PluginTool:
    manifest = ToolManifest(
        name="plugin.echo",
        version="0.1.0",
        description="Plugin tool.",
        permission_scope=Scope.FS_READ.value,
        idempotent=True,
    )
    ArgsModel = PluginArgs

    async def run(self, args: PluginArgs, ctx: ToolContext) -> ToolResult:
        return ToolResult(ok=True, data={"message": args.message, "dry_run": ctx.dry_run})


def build_plugin_tool(settings: ReflexorSettings) -> object:
    _ = settings

    class FactoryTool:
        manifest = ToolManifest(
            name="plugin.factory",
            version="0.1.0",
            description="Factory plugin tool.",
            permission_scope=Scope.FS_READ.value,
            idempotent=True,
        )
        ArgsModel = PluginArgs

        async def run(self, args: PluginArgs, ctx: ToolContext) -> ToolResult:
            return ToolResult(ok=True, data={"echo": args.message, "root": str(ctx.workspace_root)})

    return FactoryTool()


def test_load_entrypoints_is_disabled_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(**params: object) -> object:  # pragma: no cover
        raise AssertionError(f"entry_points should not be called, got params={params}")

    monkeypatch.setattr(importlib.metadata, "entry_points", _boom)

    settings = ReflexorSettings(workspace_root=tmp_path, enable_tool_entrypoints=False)
    registry = ToolRegistry()

    assert registry.load_entrypoints(settings=settings) == 0


def test_load_entrypoints_registers_tools_from_instance_and_factory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    eps = [
        FakeEntryPoint(name="plugin-1", obj=PluginTool()),
        FakeEntryPoint(name="plugin-2", obj=build_plugin_tool),
    ]

    def _fake_entry_points(**params: object) -> object:
        assert params.get("group") == "reflexor.tools"
        return eps

    monkeypatch.setattr(importlib.metadata, "entry_points", _fake_entry_points)

    settings = ReflexorSettings(workspace_root=tmp_path, enable_tool_entrypoints=True)
    registry = ToolRegistry()

    assert registry.load_entrypoints(settings=settings) == 2

    tool_1 = registry.get("plugin.echo")
    tool_2 = registry.get("plugin.factory")
    assert tool_1.manifest.sdk_version == TOOL_SDK_VERSION
    assert tool_2.manifest.sdk_version == TOOL_SDK_VERSION


def test_load_entrypoints_rejects_unknown_scopes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class BadScopeTool:
        manifest = ToolManifest(
            name="plugin.bad_scope",
            version="0.1.0",
            description="Bad scope.",
            permission_scope="nope.scope",
        )
        ArgsModel = PluginArgs

        async def run(self, args: PluginArgs, ctx: ToolContext) -> ToolResult:
            _ = args, ctx
            return ToolResult(ok=True, data={})

    eps = [FakeEntryPoint(name="bad-scope", obj=BadScopeTool())]

    def _fake_entry_points(**params: object) -> object:
        assert params.get("group") == "reflexor.tools"
        return eps

    monkeypatch.setattr(importlib.metadata, "entry_points", _fake_entry_points)

    settings = ReflexorSettings(workspace_root=tmp_path, enable_tool_entrypoints=True)
    registry = ToolRegistry()

    with pytest.raises(ValueError, match=r"unknown permission_scope"):
        registry.load_entrypoints(settings=settings)


def test_load_entrypoints_rejects_unsupported_sdk_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class BadSdkTool:
        manifest = ToolManifest(
            sdk_version=TOOL_SDK_VERSION + 1,
            name="plugin.bad_sdk",
            version="0.1.0",
            description="Bad SDK.",
            permission_scope=Scope.FS_READ.value,
        )
        ArgsModel = PluginArgs

        async def run(self, args: PluginArgs, ctx: ToolContext) -> ToolResult:
            _ = args, ctx
            return ToolResult(ok=True, data={})

    eps = [FakeEntryPoint(name="bad-sdk", obj=BadSdkTool())]

    def _fake_entry_points(**params: object) -> object:
        assert params.get("group") == "reflexor.tools"
        return eps

    monkeypatch.setattr(importlib.metadata, "entry_points", _fake_entry_points)

    settings = ReflexorSettings(workspace_root=tmp_path, enable_tool_entrypoints=True)
    registry = ToolRegistry()

    with pytest.raises(ValueError, match=r"unsupported sdk_version"):
        registry.load_entrypoints(settings=settings)


def test_load_entrypoints_rejects_duplicate_tool_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class ToolA:
        manifest = ToolManifest(
            name="plugin.dup",
            version="0.1.0",
            description="Dup A.",
            permission_scope=Scope.FS_READ.value,
        )
        ArgsModel = PluginArgs

        async def run(self, args: PluginArgs, ctx: ToolContext) -> ToolResult:
            _ = args, ctx
            return ToolResult(ok=True, data={})

    class ToolB:
        manifest = ToolManifest(
            name="plugin.dup",
            version="0.1.0",
            description="Dup B.",
            permission_scope=Scope.FS_READ.value,
        )
        ArgsModel = PluginArgs

        async def run(self, args: PluginArgs, ctx: ToolContext) -> ToolResult:
            _ = args, ctx
            return ToolResult(ok=True, data={})

    eps = [
        FakeEntryPoint(name="dup-a", obj=ToolA()),
        FakeEntryPoint(name="dup-b", obj=ToolB()),
    ]

    def _fake_entry_points(**params: object) -> object:
        assert params.get("group") == "reflexor.tools"
        return eps

    monkeypatch.setattr(importlib.metadata, "entry_points", _fake_entry_points)

    settings = ReflexorSettings(workspace_root=tmp_path, enable_tool_entrypoints=True)
    registry = ToolRegistry()

    with pytest.raises(ValueError, match=r"duplicate tool name"):
        registry.load_entrypoints(settings=settings)

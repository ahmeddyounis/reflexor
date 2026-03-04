from __future__ import annotations

import importlib.metadata
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

import reflexor.tools.registry as registry_module
from reflexor.config import ReflexorSettings
from reflexor.security.scopes import Scope
from reflexor.tools.registry import ToolRegistry
from reflexor.tools.sdk import TOOL_SDK_VERSION, ToolContext, ToolManifest, ToolResult


class FakeDist:
    def __init__(self, name: str) -> None:
        self.name = name


class FakeEntryPoint:
    def __init__(self, *, name: str, obj: object, dist_name: str = "fake-plugin") -> None:
        self.name = name
        self._obj = obj
        self.dist = FakeDist(dist_name)
        self.load_called = False

    def load(self) -> object:
        self.load_called = True
        return self._obj


class LogSink:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def warning(self, event: str, **kwargs: object) -> None:
        self.events.append((event, dict(kwargs)))


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

    settings = ReflexorSettings(
        workspace_root=tmp_path,
        profile="prod",
        enable_tool_entrypoints=True,
    )
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


def test_load_entrypoints_rejects_unsupported_sdk_version_in_prod(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class BadSdkTool:
        manifest = ToolManifest(
            sdk_version="2.0",
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

    settings = ReflexorSettings(
        workspace_root=tmp_path,
        profile="prod",
        enable_tool_entrypoints=True,
    )
    registry = ToolRegistry()

    with pytest.raises(ValueError, match=r"unsupported sdk_version"):
        registry.load_entrypoints(settings=settings)


def test_load_entrypoints_warns_and_allows_unsupported_sdk_version_in_dev_when_opted_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class BadSdkTool:
        manifest = ToolManifest(
            sdk_version="2.0",
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

    settings = ReflexorSettings(
        workspace_root=tmp_path,
        profile="dev",
        enable_tool_entrypoints=True,
        allow_unsupported_tools=True,
    )
    registry = ToolRegistry()

    with pytest.warns(UserWarning, match=r"unsupported sdk_version"):
        assert registry.load_entrypoints(settings=settings) == 1
    assert registry.get("plugin.bad_sdk").manifest.sdk_version == "2.0"


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


def test_load_entrypoints_refuses_blocked_packages_without_importing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sink = LogSink()
    monkeypatch.setattr(registry_module, "_logger", sink)

    ep = FakeEntryPoint(name="blocked", obj=PluginTool(), dist_name="Bad_Pkg")
    eps = [ep]

    def _fake_entry_points(**params: object) -> object:
        assert params.get("group") == "reflexor.tools"
        return eps

    monkeypatch.setattr(importlib.metadata, "entry_points", _fake_entry_points)

    settings = ReflexorSettings(
        workspace_root=tmp_path,
        profile="prod",
        enable_tool_entrypoints=True,
        blocked_tool_packages=["bad-pkg"],
    )
    registry = ToolRegistry()

    assert registry.load_entrypoints(settings=settings) == 0
    assert ep.load_called is False
    assert sink.events[0][0] == "tool_entrypoint_refused"
    assert sink.events[0][1]["reason"] == "blocked_package"


def test_load_entrypoints_refuses_untrusted_packages_in_prod_when_allowlist_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sink = LogSink()
    monkeypatch.setattr(registry_module, "_logger", sink)

    ep = FakeEntryPoint(name="untrusted", obj=PluginTool(), dist_name="untrusted-pkg")
    eps = [ep]

    def _fake_entry_points(**params: object) -> object:
        assert params.get("group") == "reflexor.tools"
        return eps

    monkeypatch.setattr(importlib.metadata, "entry_points", _fake_entry_points)

    settings = ReflexorSettings(
        workspace_root=tmp_path,
        profile="prod",
        enable_tool_entrypoints=True,
        trusted_tool_packages=["trusted-pkg"],
    )
    registry = ToolRegistry()

    assert registry.load_entrypoints(settings=settings) == 0
    assert ep.load_called is False
    assert sink.events[0][0] == "tool_entrypoint_refused"
    assert sink.events[0][1]["reason"] == "untrusted_package"


def test_load_entrypoints_denylist_wins_over_allowlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sink = LogSink()
    monkeypatch.setattr(registry_module, "_logger", sink)

    ep = FakeEntryPoint(name="blocked", obj=PluginTool(), dist_name="trusted-pkg")
    eps = [ep]

    def _fake_entry_points(**params: object) -> object:
        assert params.get("group") == "reflexor.tools"
        return eps

    monkeypatch.setattr(importlib.metadata, "entry_points", _fake_entry_points)

    settings = ReflexorSettings(
        workspace_root=tmp_path,
        profile="prod",
        enable_tool_entrypoints=True,
        trusted_tool_packages=["trusted-pkg"],
        blocked_tool_packages=["trusted-pkg"],
    )
    registry = ToolRegistry()

    assert registry.load_entrypoints(settings=settings) == 0
    assert ep.load_called is False
    assert sink.events[0][0] == "tool_entrypoint_refused"
    assert sink.events[0][1]["reason"] == "blocked_package"

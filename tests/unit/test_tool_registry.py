from __future__ import annotations

import pytest
from pydantic import BaseModel

from reflexor.tools.registry import ToolRegistry
from reflexor.tools.sdk import ToolContext, ToolManifest, ToolResult


class DummyArgs(BaseModel):
    message: str = "hello"


class DummyTool:
    manifest = ToolManifest(
        name="dummy.tool",
        version="0.1.0",
        description="Dummy tool for tests.",
        permission_scope="fs.read",
        idempotent=True,
    )
    ArgsModel = DummyArgs

    async def run(self, args: DummyArgs, ctx: ToolContext) -> ToolResult:
        return ToolResult(ok=True, data={"message": args.message, "dry_run": ctx.dry_run})


class DummyToolV2:
    manifest = ToolManifest(
        name="dummy.tool",
        version="0.2.0",
        description="Dummy tool override.",
        permission_scope="fs.read",
        idempotent=True,
    )
    ArgsModel = DummyArgs

    async def run(self, args: DummyArgs, ctx: ToolContext) -> ToolResult:
        return ToolResult(ok=True, data={"message": f"v2:{args.message}", "dry_run": ctx.dry_run})


class OtherTool:
    manifest = ToolManifest(
        name="other.tool",
        version="0.1.0",
        description="Other tool.",
        permission_scope="fs.read",
        idempotent=True,
    )
    ArgsModel = DummyArgs

    async def run(self, args: DummyArgs, ctx: ToolContext) -> ToolResult:
        return ToolResult(ok=True, data={"ok": True})


def test_registry_register_and_lookup() -> None:
    registry = ToolRegistry()
    tool = DummyTool()
    registry.register(tool)

    assert registry.get("dummy.tool") is tool
    assert [m.name for m in registry.list_manifests()] == ["dummy.tool"]

    registry.validate_exists("dummy.tool")


def test_registry_rejects_duplicate_name() -> None:
    registry = ToolRegistry()
    registry.register(DummyTool())

    with pytest.raises(ValueError, match="already registered"):
        registry.register(DummyToolV2())


def test_registry_lookup_unknown_tool_raises() -> None:
    registry = ToolRegistry()

    with pytest.raises(KeyError, match="unknown tool"):
        registry.get("missing.tool")

    with pytest.raises(KeyError, match="unknown tool"):
        registry.validate_exists("missing.tool")


def test_registry_override_replaces_and_restores() -> None:
    registry = ToolRegistry()
    original = DummyTool()
    override_tool = DummyToolV2()

    registry.register(original)

    with registry.override("dummy.tool", override_tool):
        assert registry.get("dummy.tool") is override_tool

    assert registry.get("dummy.tool") is original


def test_registry_override_can_insert_and_remove() -> None:
    registry = ToolRegistry()
    other = OtherTool()

    with registry.override("other.tool", other):
        assert registry.get("other.tool") is other

    with pytest.raises(KeyError, match="unknown tool"):
        registry.get("other.tool")


def test_registry_override_requires_matching_name() -> None:
    registry = ToolRegistry()
    other = OtherTool()

    with pytest.raises(ValueError, match="must match tool.manifest.name"):
        with registry.override("dummy.tool", other):
            pass

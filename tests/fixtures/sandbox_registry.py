from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from reflexor.config import ReflexorSettings
from reflexor.tools.mock_tool import MockTool
from reflexor.tools.registry import ToolRegistry
from reflexor.tools.sdk import ToolContext, ToolManifest, ToolResult


class EnvProbeArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str


class EnvProbeTool:
    name = "tests.env_probe"
    manifest = ToolManifest(
        name=name,
        version="0.1.0",
        description="Test tool: return the value of a specific environment variable.",
        permission_scope="fs.read",
        side_effects=False,
        idempotent=True,
        default_timeout_s=5,
        max_output_bytes=4_000,
        tags=["tests"],
    )
    ArgsModel = EnvProbeArgs

    async def run(self, args: EnvProbeArgs, ctx: ToolContext) -> ToolResult:
        _ = ctx
        value = os.environ.get(args.name)
        return ToolResult(ok=True, data={"name": args.name, "value": value})


class SleepArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    seconds: float = Field(ge=0.0, le=60.0)


class SleepTool:
    name = "tests.sleep"
    manifest = ToolManifest(
        name=name,
        version="0.1.0",
        description="Test tool: sleep for N seconds.",
        permission_scope="fs.read",
        side_effects=False,
        idempotent=True,
        default_timeout_s=5,
        max_output_bytes=4_000,
        tags=["tests"],
    )
    ArgsModel = SleepArgs

    async def run(self, args: SleepArgs, ctx: ToolContext) -> ToolResult:
        await asyncio.sleep(float(args.seconds))
        return ToolResult(
            ok=True, data={"slept_s": float(args.seconds), "dry_run": bool(ctx.dry_run)}
        )


def build_registry(settings: ReflexorSettings) -> ToolRegistry:
    _ = settings
    registry = ToolRegistry()
    registry.register(EnvProbeTool())
    registry.register(SleepTool())
    registry.register(MockTool(tool_name="tests.mock", permission_scope="fs.read"))
    return registry


if TYPE_CHECKING:
    from reflexor.tools.sdk.tool import Tool

    _tool_1: Tool[EnvProbeArgs] = EnvProbeTool()
    _tool_2: Tool[SleepArgs] = SleepTool()

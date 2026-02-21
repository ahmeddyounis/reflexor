from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from reflexor.tools.sdk.contracts import ToolManifest, ToolResult
from reflexor.tools.sdk.tool import ToolContext


class EchoArgs(BaseModel):
    """Arguments for `debug.echo`.

    This tool exists primarily to validate the tool boundary; it accepts arbitrary JSON-ish
    key/value pairs.
    """

    model_config = ConfigDict(extra="allow")


class EchoTool:
    """Debug-only tool that returns its input arguments.

    This exists as a minimal concrete implementation to validate the tools package layout.
    """

    name = "debug.echo"
    manifest = ToolManifest(
        name=name,
        version="0.1.0",
        description="Echo tool call args (debug-only).",
        permission_scope="fs.read",
        side_effects=False,
        idempotent=True,
        default_timeout_s=5,
        max_output_bytes=8_000,
        tags=["debug"],
    )

    ArgsModel = EchoArgs

    async def run(self, args: EchoArgs, ctx: ToolContext) -> ToolResult:
        return ToolResult(
            ok=True,
            data={
                "tool_name": self.manifest.name,
                "dry_run": ctx.dry_run,
                "args": args.model_dump(),
            },
        )


if TYPE_CHECKING:
    from reflexor.tools.sdk.tool import Tool

    _tool: Tool[EchoArgs] = EchoTool()

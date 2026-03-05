from __future__ import annotations

import asyncio
from dataclasses import dataclass

from pydantic import BaseModel

from reflexor.config import ReflexorSettings
from reflexor.tools.sdk import Tool, ToolContext, ToolResult


@dataclass(frozen=True, slots=True)
class InProcessBackend:
    """Execute tools directly in the current Python process."""

    async def execute(
        self,
        *,
        tool: Tool[BaseModel],
        args: BaseModel,
        ctx: ToolContext,
        settings: ReflexorSettings,
    ) -> ToolResult:
        _ = settings
        try:
            return await asyncio.wait_for(tool.run(args, ctx), timeout=ctx.timeout_s)
        except TimeoutError:
            return ToolResult(
                ok=False,
                error_code="TIMEOUT",
                error_message=f"tool execution exceeded timeout_s={ctx.timeout_s}",
            )
        except Exception as exc:
            return ToolResult(
                ok=False,
                error_code="TOOL_ERROR",
                error_message=f"tool raised {type(exc).__name__}",
                debug={"exception": repr(exc)},
            )

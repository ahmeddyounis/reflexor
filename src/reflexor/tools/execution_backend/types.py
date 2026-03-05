from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

from reflexor.config import ReflexorSettings
from reflexor.tools.sdk import Tool, ToolContext, ToolResult


class ToolExecutionBackend(Protocol):
    async def execute(
        self,
        *,
        tool: Tool[BaseModel],
        args: BaseModel,
        ctx: ToolContext,
        settings: ReflexorSettings,
    ) -> ToolResult: ...

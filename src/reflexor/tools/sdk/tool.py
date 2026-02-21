from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypeVar

from pydantic import BaseModel

from reflexor.tools.sdk.contracts import ToolManifest, ToolResult

ArgsT = TypeVar("ArgsT", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class ToolContext:
    """Minimal execution context passed to tools.

    The context intentionally stays small (Interface Segregation Principle). It is expected to
    grow carefully as tool needs emerge, but it should not couple tools to infrastructure
    concerns like queues or databases.
    """

    workspace_root: Path
    dry_run: bool = True

    event_id: str | None = None
    run_id: str | None = None
    task_id: str | None = None
    tool_call_id: str | None = None


class Tool(Protocol[ArgsT]):
    """Boundary interface for tool implementations.

    Implementations may perform side effects, but must not do so at import time.
    """

    manifest: ToolManifest
    ArgsModel: type[ArgsT]

    async def run(self, args: ArgsT, ctx: ToolContext) -> ToolResult:
        """Execute a validated tool call and return a JSON-serializable result."""

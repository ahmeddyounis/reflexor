from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, TypeVar

from pydantic import BaseModel

from reflexor.observability.context import get_correlation_ids
from reflexor.security.secrets import SecretsProvider
from reflexor.tools.sdk.contracts import ToolManifest, ToolResult

ArgsT = TypeVar("ArgsT", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class ToolContext:
    """Execution context passed to tools (DI-friendly).

    The context intentionally stays small (Interface Segregation Principle). It is expected to
    grow carefully as tool needs emerge, but it should not couple tools to infrastructure
    concerns like queues or databases.
    """

    workspace_root: Path
    dry_run: bool = True
    timeout_s: float = 60.0
    correlation_ids: dict[str, str | None] = field(default_factory=get_correlation_ids)
    secrets_provider: SecretsProvider | None = None

    def __post_init__(self) -> None:
        if not self.workspace_root.is_absolute():
            raise ValueError("workspace_root must be an absolute path")
        if self.timeout_s <= 0:
            raise ValueError("timeout_s must be > 0")


class Tool(Protocol[ArgsT]):
    """Boundary interface for tool implementations.

    Implementations may perform side effects, but must not do so at import time.
    """

    manifest: ToolManifest
    ArgsModel: type[ArgsT]

    async def run(self, args: ArgsT, ctx: ToolContext) -> ToolResult:
        """Execute a validated tool call and return a JSON-serializable result."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict

from reflexor.tools.sdk import ToolContext, ToolManifest, ToolResult


class RecordedArgs(BaseModel):
    """Permissive args model for replay tools (accepts arbitrary JSON)."""

    model_config = ConfigDict(extra="allow", frozen=True)


@dataclass(frozen=True, slots=True)
class ReplayInvocation:
    tool_name: str
    tool_call_id: str | None
    called_at_ms: int
    dry_run: bool
    result: ToolResult


@dataclass(slots=True)
class _RecordedResultTool:
    tool_name: str
    permission_scope: str
    results_by_tool_call_id: dict[str, ToolResult]
    now_ms: Callable[[], int]

    manifest: ToolManifest = field(init=False)
    invocations: list[ReplayInvocation] = field(default_factory=list)

    ArgsModel = RecordedArgs

    def __post_init__(self) -> None:
        self.manifest = ToolManifest(
            name=self.tool_name,
            version="0.1.0",
            description="Replay tool returning recorded ToolResults.",
            permission_scope=self.permission_scope,
            side_effects=False,
            idempotent=False,
            default_timeout_s=5,
            max_output_bytes=64_000,
            tags=["replay", "mock"],
        )

    async def run(self, args: RecordedArgs, ctx: ToolContext) -> ToolResult:
        _ = args
        tool_call_id = ctx.correlation_ids.get("tool_call_id")
        tool_call_id_str = tool_call_id if isinstance(tool_call_id, str) else None
        result = (
            self.results_by_tool_call_id.get(tool_call_id_str)
            if tool_call_id_str is not None
            else None
        )
        if result is None:
            result = ToolResult(
                ok=False,
                error_code="REPLAY_MISSING_RESULT",
                error_message="no recorded ToolResult found for tool_call_id",
                debug={"tool_call_id": tool_call_id},
            )

        self.invocations.append(
            ReplayInvocation(
                tool_name=self.tool_name,
                tool_call_id=tool_call_id_str,
                called_at_ms=int(self.now_ms()),
                dry_run=bool(ctx.dry_run),
                result=result,
            )
        )
        return result


@dataclass(slots=True)
class _AlwaysOkTool:
    tool_name: str
    permission_scope: str
    now_ms: Callable[[], int]

    manifest: ToolManifest = field(init=False)
    invocations: list[ReplayInvocation] = field(default_factory=list)

    ArgsModel = RecordedArgs

    def __post_init__(self) -> None:
        self.manifest = ToolManifest(
            name=self.tool_name,
            version="0.1.0",
            description="Replay tool returning ok=true.",
            permission_scope=self.permission_scope,
            side_effects=False,
            idempotent=False,
            default_timeout_s=5,
            max_output_bytes=64_000,
            tags=["replay", "mock"],
        )

    async def run(self, args: RecordedArgs, ctx: ToolContext) -> ToolResult:
        _ = args
        tool_call_id = ctx.correlation_ids.get("tool_call_id")
        tool_call_id_str = tool_call_id if isinstance(tool_call_id, str) else None
        result = ToolResult(
            ok=True,
            data={"tool_call_id": tool_call_id, "tool_name": self.tool_name},
        )
        self.invocations.append(
            ReplayInvocation(
                tool_name=self.tool_name,
                tool_call_id=tool_call_id_str,
                called_at_ms=int(self.now_ms()),
                dry_run=bool(ctx.dry_run),
                result=result,
            )
        )
        return result

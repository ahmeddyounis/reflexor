from __future__ import annotations

from reflexor.domain.models import ToolCall
from reflexor.tools.sdk.contracts import ToolManifest, ToolResult


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

    def execute(self, call: ToolCall) -> ToolResult:
        return ToolResult(ok=True, data={"tool_name": call.tool_name, "args": call.args})

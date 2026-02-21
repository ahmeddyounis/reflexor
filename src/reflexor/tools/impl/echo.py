from __future__ import annotations

from reflexor.domain.models import ToolCall
from reflexor.tools.sdk.tool import ToolOutput


class EchoTool:
    """Debug-only tool that returns its input arguments.

    This exists as a minimal concrete implementation to validate the tools package layout.
    """

    name = "debug.echo"

    def execute(self, call: ToolCall) -> ToolOutput:
        return {"tool_name": call.tool_name, "args": call.args}

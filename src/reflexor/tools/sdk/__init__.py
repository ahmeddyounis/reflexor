"""Tool SDK (boundary interfaces).

This subpackage defines tool-facing interfaces and types. It must stay framework-agnostic and free
of side effects at import time.
"""

from __future__ import annotations

from reflexor.tools.sdk.compat import (
    SUPPORTED_TOOL_SDK_VERSIONS,
    TOOL_SDK_VERSION,
    is_supported_tool_sdk_version,
)
from reflexor.tools.sdk.contracts import ToolManifest, ToolResult
from reflexor.tools.sdk.tool import Tool, ToolContext

__all__ = [
    "SUPPORTED_TOOL_SDK_VERSIONS",
    "TOOL_SDK_VERSION",
    "Tool",
    "ToolContext",
    "ToolManifest",
    "ToolResult",
    "is_supported_tool_sdk_version",
]

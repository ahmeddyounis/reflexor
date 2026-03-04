from __future__ import annotations

from reflexor.config import ReflexorSettings
from reflexor.tools.fs_tool import FsListDirTool, FsReadTextTool, FsWriteTextTool
from reflexor.tools.http_tool import HttpTool
from reflexor.tools.registry import ToolRegistry
from reflexor.tools.webhook_tool import WebhookEmitTool


def build_builtin_registry(*, settings: ReflexorSettings) -> ToolRegistry:
    """Build the default ToolRegistry used by in-process and sandbox runners."""

    registry = ToolRegistry()
    registry.register(FsReadTextTool(settings=settings))
    registry.register(FsWriteTextTool(settings=settings))
    registry.register(FsListDirTool(settings=settings))
    registry.register(HttpTool(settings=settings))
    registry.register(WebhookEmitTool(settings=settings))
    registry.load_entrypoints(settings=settings)
    return registry


__all__ = ["build_builtin_registry"]

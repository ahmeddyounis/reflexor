from __future__ import annotations

from reflexor.config import ReflexorSettings
from reflexor.tools.impl.fs import FsListDirTool, FsReadTextTool, FsWriteTextTool
from reflexor.tools.impl.http import HttpTool
from reflexor.tools.impl.webhook import WebhookEmitTool
from reflexor.tools.registry import ToolRegistry


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

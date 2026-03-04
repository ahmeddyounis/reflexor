"""Bootstrap wiring for tool execution."""

from __future__ import annotations

from reflexor.config import ReflexorSettings
from reflexor.tools.registry import ToolRegistry
from reflexor.tools.runner import ToolRunner
from reflexor.tools.sandbox_policy import SandboxPolicy, SandboxPolicyBackend


def build_tool_runner(
    settings: ReflexorSettings,
    *,
    registry: ToolRegistry,
) -> ToolRunner:
    sandbox_policy = SandboxPolicy.from_settings(settings)
    sandbox_backend = SandboxPolicyBackend(policy=sandbox_policy)
    return ToolRunner(
        registry=registry,
        settings=settings,
        backend=sandbox_backend,
    )

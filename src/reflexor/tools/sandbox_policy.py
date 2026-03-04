from __future__ import annotations

import sys
from collections.abc import Mapping
from dataclasses import dataclass, field

from pydantic import BaseModel

from reflexor.config import ReflexorSettings
from reflexor.tools.execution_backend import (
    InProcessBackend,
    SubprocessSandboxBackend,
    ToolExecutionBackend,
)
from reflexor.tools.sdk import Tool, ToolContext, ToolResult


@dataclass(frozen=True, slots=True)
class SandboxPolicy:
    """Policy for selecting tool execution backend per tool name."""

    enabled: bool = False
    tools: frozenset[str] = frozenset()
    env_allowlist: tuple[str, ...] = ()
    max_memory_mb: int | None = None
    python_executable: str = field(default_factory=lambda: sys.executable)

    @classmethod
    def from_settings(cls, settings: ReflexorSettings) -> SandboxPolicy:
        python_executable = settings.sandbox_python_executable or sys.executable
        return cls(
            enabled=bool(settings.sandbox_enabled),
            tools=frozenset(settings.sandbox_tools),
            env_allowlist=tuple(settings.sandbox_env_allowlist),
            max_memory_mb=settings.sandbox_max_memory_mb,
            python_executable=python_executable,
        )

    def should_sandbox(self, tool_name: str) -> bool:
        if not self.enabled:
            return False
        return tool_name in self.tools


@dataclass(slots=True)
class SandboxPolicyBackend:
    """ToolExecutionBackend that selects in-process vs sandbox per tool name."""

    policy: SandboxPolicy
    registry_factory: str | None = None
    extra_env: Mapping[str, str] = field(default_factory=dict)
    in_process_backend: ToolExecutionBackend = field(default_factory=InProcessBackend)

    _sandbox_backend: SubprocessSandboxBackend = field(init=False)

    def __post_init__(self) -> None:
        self._sandbox_backend = SubprocessSandboxBackend(
            registry_factory=self.registry_factory,
            env_allowlist=self.policy.env_allowlist,
            extra_env=self.extra_env,
            python_executable=self.policy.python_executable,
            max_memory_mb=self.policy.max_memory_mb,
        )

    async def execute(
        self,
        *,
        tool: Tool[BaseModel],
        args: BaseModel,
        ctx: ToolContext,
        settings: ReflexorSettings,
    ) -> ToolResult:
        backend = self.in_process_backend
        if self.policy.should_sandbox(tool.manifest.name):
            backend = self._sandbox_backend
        return await backend.execute(tool=tool, args=args, ctx=ctx, settings=settings)


__all__ = ["SandboxPolicy", "SandboxPolicyBackend"]

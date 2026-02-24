"""Concurrency limiter for the executor (pure, testable).

The executor should cap both:
- global in-flight tool calls
- per-tool in-flight tool calls

This module provides a small, dependency-light `ConcurrencyLimiter` that can be used as:

```py
async with limiter.limit(tool_name):
    ...
```

Clean Architecture:
- No DB/queue imports (SRP).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ConcurrencyLimits:
    """Executor concurrency limits (global + per-tool)."""

    max_global: int = 50
    per_tool: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        max_global = int(self.max_global)
        if max_global <= 0:
            raise ValueError("max_global must be > 0")
        object.__setattr__(self, "max_global", max_global)

        normalized: dict[str, int] = {}
        for tool_name, raw_limit in self.per_tool.items():
            name = tool_name.strip()
            if not name:
                raise ValueError("per_tool keys must be non-empty")
            limit = int(raw_limit)
            if limit <= 0:
                raise ValueError("per_tool values must be > 0")
            if limit > max_global:
                raise ValueError("per_tool values must be <= max_global")
            if name in normalized:
                raise ValueError("per_tool contains duplicate tool names after normalization")
            normalized[name] = limit
        object.__setattr__(self, "per_tool", normalized)


class ConcurrencyLimiter:
    """Async concurrency limiter (global + per-tool semaphores)."""

    def __init__(
        self,
        *,
        max_global: int,
        per_tool: Mapping[str, int] | None = None,
    ) -> None:
        limits = ConcurrencyLimits(max_global=max_global, per_tool=dict(per_tool or {}))
        self._limits = limits
        self._global = asyncio.Semaphore(limits.max_global)
        self._per_tool = {name: asyncio.Semaphore(limit) for name, limit in limits.per_tool.items()}

    @property
    def limits(self) -> ConcurrencyLimits:
        return self._limits

    @asynccontextmanager
    async def limit(self, tool_name: str) -> AsyncIterator[None]:
        """Acquire concurrency slots for `tool_name` (global + per-tool)."""

        normalized = tool_name.strip()
        if not normalized:
            raise ValueError("tool_name must be non-empty")

        tool_sem = self._per_tool.get(normalized)
        tool_acquired = False

        await self._global.acquire()
        try:
            if tool_sem is not None:
                await tool_sem.acquire()
                tool_acquired = True

            try:
                yield
            finally:
                if tool_acquired and tool_sem is not None:
                    tool_sem.release()
        finally:
            self._global.release()


__all__ = ["ConcurrencyLimiter", "ConcurrencyLimits"]

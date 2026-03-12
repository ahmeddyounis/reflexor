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
    """Async concurrency limiter with atomic global + per-tool admission."""

    def __init__(
        self,
        *,
        max_global: int,
        per_tool: Mapping[str, int] | None = None,
    ) -> None:
        limits = ConcurrencyLimits(max_global=max_global, per_tool=dict(per_tool or {}))
        self._limits = limits
        self._condition = asyncio.Condition()
        self._in_flight_total = 0
        self._in_flight_by_tool: dict[str, int] = {}

    @property
    def limits(self) -> ConcurrencyLimits:
        return self._limits

    @asynccontextmanager
    async def limit(self, tool_name: str) -> AsyncIterator[None]:
        """Acquire concurrency slots for `tool_name` (global + per-tool)."""

        normalized = tool_name.strip()
        if not normalized:
            raise ValueError("tool_name must be non-empty")
        await self._acquire(normalized)
        try:
            yield
        finally:
            await self._release(normalized)

    async def _acquire(self, tool_name: str) -> None:
        async with self._condition:
            await self._condition.wait_for(lambda: self._has_capacity(tool_name))
            self._in_flight_total += 1
            self._in_flight_by_tool[tool_name] = self._in_flight_by_tool.get(tool_name, 0) + 1

    async def _release(self, tool_name: str) -> None:
        async with self._condition:
            self._in_flight_total -= 1
            remaining = self._in_flight_by_tool.get(tool_name, 0) - 1
            if remaining > 0:
                self._in_flight_by_tool[tool_name] = remaining
            else:
                self._in_flight_by_tool.pop(tool_name, None)
            self._condition.notify_all()

    def _has_capacity(self, tool_name: str) -> bool:
        if self._in_flight_total >= self._limits.max_global:
            return False
        tool_limit = self._limits.per_tool.get(tool_name)
        if tool_limit is None:
            return True
        return self._in_flight_by_tool.get(tool_name, 0) < tool_limit


__all__ = ["ConcurrencyLimiter", "ConcurrencyLimits"]

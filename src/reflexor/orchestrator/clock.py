"""Clock utilities for orchestrator code.

Clean Architecture:
- Orchestrator is application-layer code.
- This module may depend on stdlib only.
- Forbidden: FastAPI/SQLAlchemy/httpx/worker/API/CLI imports.
"""

from __future__ import annotations

import asyncio
import time
from typing import Protocol


class Clock(Protocol):
    """Clock abstraction for deterministic tests and orchestration code."""

    def now_ms(self) -> int: ...

    def monotonic_ms(self) -> int: ...

    async def sleep(self, seconds: float) -> None: ...


class SystemClock:
    def now_ms(self) -> int:
        return int(time.time() * 1000)

    def monotonic_ms(self) -> int:
        return int(time.monotonic() * 1000)

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


__all__ = ["Clock", "SystemClock"]

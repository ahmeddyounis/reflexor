"""Budget primitives for orchestrated runs.

Budgets are intended to bound resource usage (time, number of tasks, tool calls, etc.) at the
application/orchestrator layer.

Clean Architecture:
- Orchestrator is application-layer code.
- This module may depend on `reflexor.domain` and stdlib.
- Forbidden: FastAPI/SQLAlchemy/httpx/worker/API/CLI imports.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RunBudget:
    """Run-level budget caps (placeholders; enforced later)."""

    max_tasks: int | None = None
    max_tool_calls: int | None = None
    max_runtime_s: float | None = None


__all__ = ["RunBudget"]

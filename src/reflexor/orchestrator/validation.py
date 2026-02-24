"""Orchestrator-level validation helpers (placeholder).

This module is intended to host checks that are specific to orchestration (plan validation, budget
checks, trigger sanity checks), without leaking infrastructure concerns into the domain layer.

Clean Architecture:
- Orchestrator is application-layer code.
- This module may depend on `reflexor.domain`, `reflexor.config`, and stdlib.
- Forbidden: FastAPI/SQLAlchemy/httpx/worker/API/CLI imports.
"""

from __future__ import annotations

from reflexor.domain.models import Task


def validate_task_name(task: Task) -> None:
    """Raise `ValueError` if a task name is invalid for orchestration (placeholder)."""

    if not task.name.strip():
        raise ValueError("task.name must be non-empty")


__all__ = ["validate_task_name"]

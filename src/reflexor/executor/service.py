"""Executor service scaffolding.

The executor is application-layer code: it pulls work from the queue, enforces policy, executes
tools, and persists results. This module provides the DI-friendly seam where those dependencies are
composed.

Forbidden imports: FastAPI/Starlette and CLI entrypoints.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ExecutorService:
    """Placeholder executor service.

    Concrete execution logic will be implemented in later milestones.
    """

    def run_once(self) -> None:
        raise NotImplementedError("ExecutorService is scaffolding only (not yet implemented).")

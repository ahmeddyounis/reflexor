"""Plan primitives for orchestrated runs.

Plans are produced by planning logic and consumed by executors/orchestrators.

Clean Architecture:
- Orchestrator is application-layer code.
- This module may depend on `reflexor.domain` and stdlib.
- Forbidden: FastAPI/SQLAlchemy/httpx/worker/API/CLI imports.
"""

from __future__ import annotations

from dataclasses import dataclass

from reflexor.domain.models import Task


@dataclass(frozen=True, slots=True)
class Plan:
    """A minimal plan representation (placeholder).

    Today, the domain-level `RunPacket.plan` is a JSON-safe dict. This typed representation is an
    internal convenience for future orchestrator logic.
    """

    tasks: tuple[Task, ...] = ()


__all__ = ["Plan"]

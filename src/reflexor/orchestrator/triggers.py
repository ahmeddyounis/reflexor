"""Trigger primitives.

Triggers define when an event should start (or influence) an orchestrated run. This is intentionally
minimal scaffolding; concrete trigger wiring will live in outer layers (API/worker) and call into
orchestrator interfaces.

Clean Architecture:
- Orchestrator is application-layer code.
- This module may depend on `reflexor.domain` and stdlib.
- Forbidden: FastAPI/SQLAlchemy/httpx/worker/API/CLI imports.
"""

from __future__ import annotations

from typing import Protocol

from reflexor.domain.models_event import Event


class Trigger(Protocol):
    """A trigger that decides whether it matches an event."""

    trigger_id: str

    def matches(self, event: Event) -> bool: ...


__all__ = ["Trigger"]

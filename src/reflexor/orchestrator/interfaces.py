"""Narrow interfaces for orchestrator components.

This module defines ISP-friendly interfaces (Protocols) for orchestrator building blocks so the
engine can be composed without depending on concrete implementations.

Clean Architecture:
- Orchestrator is application-layer code.
- This module may depend on `reflexor.domain`, `reflexor.config`, queue interface, and tool
  boundary contracts.
- Forbidden: FastAPI/SQLAlchemy/httpx/worker/API/CLI imports.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from reflexor.domain.models_event import Event
from reflexor.orchestrator.plans import Plan


class Planner(Protocol):
    """Planner: derive a plan from an event + context."""

    async def plan(self, event: Event) -> Plan: ...


class Reflex(Protocol):
    """Reflex: decide what to do next given an event."""

    async def decide(self, event: Event) -> dict[str, object]: ...


class TriggerRouter(Protocol):
    """Route events to eligible triggers."""

    async def route(self, event: Event) -> Sequence[str]: ...


__all__ = ["Planner", "Reflex", "TriggerRouter"]

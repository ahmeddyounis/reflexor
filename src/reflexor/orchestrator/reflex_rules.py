"""Reflex rule primitives.

Reflex rules are small decision units that help decide how to respond to an event (e.g. whether to
plan, execute, request approval, or do nothing). This file defines a narrow contract so rules stay
easy to test and extend.

Clean Architecture:
- Orchestrator is application-layer code.
- This module may depend on `reflexor.domain` and stdlib.
- Forbidden: FastAPI/SQLAlchemy/httpx/worker/API/CLI imports.
"""

from __future__ import annotations

from typing import Protocol

from reflexor.domain.models_event import Event


class ReflexRule(Protocol):
    """A single reflex rule.

    Rules are evaluated in an orchestrator-defined order. Returning `None` means "not applicable".
    """

    rule_id: str

    def evaluate(self, event: Event) -> dict[str, object] | None: ...


__all__ = ["ReflexRule"]

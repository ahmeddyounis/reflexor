from __future__ import annotations

from typing import TYPE_CHECKING

from reflexor.domain.errors import BudgetExceeded
from reflexor.domain.models_event import Event

if TYPE_CHECKING:
    from reflexor.orchestrator.engine.core import OrchestratorEngine


async def enqueue_backlog_event(engine: OrchestratorEngine, event: Event) -> None:
    async with engine._backlog_lock:
        limit = engine.limits.max_backlog_events
        if limit is not None and len(engine._backlog) + 1 > limit:
            raise BudgetExceeded(
                "backlog budget exceeded",
                budget="max_backlog_events",
                context={
                    "limit": limit,
                    "current": len(engine._backlog),
                    "would_be": len(engine._backlog) + 1,
                },
            )
        engine._backlog.append(event)


async def drain_backlog(engine: OrchestratorEngine, *, max_items: int | None = None) -> list[Event]:
    """Remove and return up to `max_items` events from the backlog."""

    async with engine._backlog_lock:
        if max_items is None:
            items = list(engine._backlog)
            engine._backlog.clear()
            return items

        if max_items <= 0:
            return []

        drained: list[Event] = []
        for _ in range(min(max_items, len(engine._backlog))):
            drained.append(engine._backlog.popleft())
        return drained

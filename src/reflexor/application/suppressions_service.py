"""Application read paths for event suppressions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from reflexor.orchestrator.clock import Clock, SystemClock
from reflexor.storage.ports import EventSuppressionRecord, EventSuppressionRepo
from reflexor.storage.uow import DatabaseSession, UnitOfWork


@dataclass(frozen=True, slots=True)
class EventSuppressionQueryService:
    uow_factory: Callable[[], UnitOfWork]
    repo: Callable[[DatabaseSession], EventSuppressionRepo]
    clock: Clock = SystemClock()

    async def list_active(
        self, *, limit: int, offset: int
    ) -> tuple[list[EventSuppressionRecord], int]:
        now_ms = int(self.clock.now_ms())
        uow = self.uow_factory()
        async with uow:
            repo = self.repo(uow.session)
            total = await repo.count_active(now_ms=now_ms)
            items = await repo.list_active(now_ms=now_ms, limit=limit, offset=offset)
            return items, total


__all__ = ["EventSuppressionQueryService"]

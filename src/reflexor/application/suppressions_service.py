"""Application services for event suppressions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from reflexor.orchestrator.clock import Clock, SystemClock
from reflexor.storage.ports import EventSuppressionRecord, EventSuppressionRepo
from reflexor.storage.uow import DatabaseSession, UnitOfWork


def _is_active_suppression(record: EventSuppressionRecord, *, now_ms: int) -> bool:
    suppressed_until_ms = record.suppressed_until_ms
    if suppressed_until_ms is None:
        return False
    return int(suppressed_until_ms) > now_ms and int(record.expires_at_ms) > now_ms


@dataclass(frozen=True, slots=True)
class EventSuppressionQueryService:
    uow_factory: Callable[[], UnitOfWork]
    repo: Callable[[DatabaseSession], EventSuppressionRepo]
    clock: Clock = field(default_factory=SystemClock)

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


@dataclass(frozen=True, slots=True)
class EventSuppressionCommandService:
    uow_factory: Callable[[], UnitOfWork]
    repo: Callable[[DatabaseSession], EventSuppressionRepo]
    clock: Clock = field(default_factory=SystemClock)

    async def clear(
        self,
        signature_hash: str,
        *,
        cleared_by: str | None,
        cleared_request_id: str | None = None,
    ) -> EventSuppressionRecord:
        normalized_hash = signature_hash.strip()
        if not normalized_hash:
            raise ValueError("signature_hash must be non-empty")

        actor = None if cleared_by is None else (cleared_by.strip() or None)
        request_id = None if cleared_request_id is None else (cleared_request_id.strip() or None)

        now_ms = int(self.clock.now_ms())
        uow = self.uow_factory()
        async with uow:
            repo = self.repo(uow.session)
            record = await repo.get(normalized_hash)
            if record is None or not _is_active_suppression(record, now_ms=now_ms):
                raise KeyError(f"event suppression not found: {normalized_hash!r}")

            updated = EventSuppressionRecord(
                signature_hash=record.signature_hash,
                event_type=record.event_type,
                event_source=record.event_source,
                signature=record.signature,
                window_start_ms=now_ms,
                count=0,
                threshold=record.threshold,
                window_ms=record.window_ms,
                suppressed_until_ms=None,
                resume_required=False,
                cleared_at_ms=now_ms,
                cleared_by=actor,
                cleared_request_id=request_id,
                created_at_ms=record.created_at_ms,
                updated_at_ms=now_ms,
                expires_at_ms=now_ms + int(record.window_ms),
            )
            return await repo.upsert(updated)


__all__ = ["EventSuppressionCommandService", "EventSuppressionQueryService"]

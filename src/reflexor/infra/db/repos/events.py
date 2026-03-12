from __future__ import annotations

from sqlalchemy import Select, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from reflexor.domain.models_event import Event
from reflexor.infra.db.mappers import event_from_orm, event_to_row_dict
from reflexor.infra.db.models import EventDedupeRow, EventRow
from reflexor.infra.db.repos._common import _normalize_optional_str, _validate_limit_offset

DEFAULT_DEDUPE_WINDOW_MS = 86_400_000


class SqlAlchemyEventRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, event: Event) -> Event:
        row = EventRow(**event_to_row_dict(event))
        self._session.add(row)
        await self._session.flush()
        return event.model_copy(deep=True)

    async def get_by_dedupe(
        self,
        *,
        source: str,
        dedupe_key: str,
        active_at_ms: int | None = None,
    ) -> Event | None:
        normalized_source = _normalize_optional_str(source)
        if normalized_source is None:
            raise ValueError("source must be non-empty")

        normalized_key = _normalize_optional_str(dedupe_key)
        if normalized_key is None:
            raise ValueError("dedupe_key must be non-empty")

        dedupe_row = await self._session.get(
            EventDedupeRow,
            {"source": normalized_source, "dedupe_key": normalized_key},
        )
        if dedupe_row is None:
            return None

        lookup_ms = int(active_at_ms) if active_at_ms is not None else None
        if lookup_ms is not None and int(dedupe_row.expires_at_ms) <= lookup_ms:
            return None

        row = await self._session.get(EventRow, dedupe_row.event_id)
        if row is None:
            return None
        return event_from_orm(row)

    async def create_or_get_by_dedupe(
        self,
        *,
        source: str,
        dedupe_key: str,
        event: Event,
        dedupe_window_ms: int | None = None,
        active_at_ms: int | None = None,
    ) -> tuple[Event, bool]:
        normalized_source = _normalize_optional_str(source)
        if normalized_source is None:
            raise ValueError("source must be non-empty")

        normalized_key = _normalize_optional_str(dedupe_key)
        if normalized_key is None:
            raise ValueError("dedupe_key must be non-empty")

        window_anchor_ms = (
            int(active_at_ms) if active_at_ms is not None else int(event.received_at_ms)
        )
        effective_window_ms = (
            DEFAULT_DEDUPE_WINDOW_MS if dedupe_window_ms is None else int(dedupe_window_ms)
        )
        if effective_window_ms <= 0:
            raise ValueError("dedupe_window_ms must be > 0 when provided")

        existing = await self.get_by_dedupe(
            source=normalized_source,
            dedupe_key=normalized_key,
            active_at_ms=window_anchor_ms,
        )
        if existing is not None:
            return existing, False

        event_to_store = event.model_copy(
            update={"source": normalized_source, "dedupe_key": normalized_key},
            deep=True,
        )
        expires_at_ms = window_anchor_ms + effective_window_ms

        integrity_error: IntegrityError | None = None
        try:
            async with self._session.begin_nested():
                self._session.add(EventRow(**event_to_row_dict(event_to_store)))
                await self._session.flush()

                dedupe_row = await self._session.get(
                    EventDedupeRow,
                    {"source": normalized_source, "dedupe_key": normalized_key},
                )
                if dedupe_row is None:
                    self._session.add(
                        EventDedupeRow(
                            source=normalized_source,
                            dedupe_key=normalized_key,
                            event_id=event_to_store.event_id,
                            created_at_ms=window_anchor_ms,
                            updated_at_ms=window_anchor_ms,
                            expires_at_ms=expires_at_ms,
                        )
                    )
                elif int(dedupe_row.expires_at_ms) > window_anchor_ms:
                    raise IntegrityError(
                        "active dedupe row exists",
                        params=None,
                        orig=RuntimeError("active dedupe row exists"),
                    )
                else:
                    dedupe_row.event_id = event_to_store.event_id
                    dedupe_row.created_at_ms = window_anchor_ms
                    dedupe_row.updated_at_ms = window_anchor_ms
                    dedupe_row.expires_at_ms = expires_at_ms

                await self._session.flush()
                return event_to_store.model_copy(deep=True), True
        except IntegrityError as exc:
            integrity_error = exc

        existing = await self.get_by_dedupe(
            source=normalized_source,
            dedupe_key=normalized_key,
            active_at_ms=window_anchor_ms,
        )
        if existing is not None:
            return existing, False

        if integrity_error is not None:  # pragma: no cover
            raise integrity_error
        raise RuntimeError("failed to create or find event by dedupe key")

    async def get(self, event_id: str) -> Event | None:
        normalized = event_id.strip()
        if not normalized:
            raise ValueError("event_id must be non-empty")

        row = await self._session.get(EventRow, normalized)
        if row is None:
            return None
        return event_from_orm(row)

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        event_type: str | None = None,
        source: str | None = None,
    ) -> list[Event]:
        limit_int, offset_int = _validate_limit_offset(limit=limit, offset=offset)
        if limit_int == 0:
            return []

        stmt: Select[tuple[EventRow]] = select(EventRow)
        if event_type is not None:
            normalized = _normalize_optional_str(event_type)
            if normalized is None:
                raise ValueError("event_type must be non-empty when provided")
            stmt = stmt.where(EventRow.type == normalized)
        if source is not None:
            normalized = _normalize_optional_str(source)
            if normalized is None:
                raise ValueError("source must be non-empty when provided")
            stmt = stmt.where(EventRow.source == normalized)

        stmt = (
            stmt.order_by(EventRow.received_at_ms, EventRow.event_id)
            .limit(limit_int)
            .offset(offset_int)
        )
        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [event_from_orm(row) for row in rows]

    async def prune_expired_dedupe(self, *, now_ms: int, limit: int) -> int:
        limit_int, _ = _validate_limit_offset(limit=limit, offset=0)
        if limit_int == 0:
            return 0

        stmt = (
            select(EventDedupeRow.source, EventDedupeRow.dedupe_key)
            .where(EventDedupeRow.expires_at_ms <= int(now_ms))
            .order_by(
                EventDedupeRow.expires_at_ms,
                EventDedupeRow.source,
                EventDedupeRow.dedupe_key,
            )
            .limit(limit_int)
        )
        pairs = list((await self._session.execute(stmt)).all())
        if not pairs:
            return 0

        for source, dedupe_key in pairs:
            await self._session.execute(
                delete(EventDedupeRow).where(
                    EventDedupeRow.source == source,
                    EventDedupeRow.dedupe_key == dedupe_key,
                )
            )
        await self._session.flush()
        return len(pairs)

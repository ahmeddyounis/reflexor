from __future__ import annotations

from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from reflexor.domain.models_event import Event
from reflexor.infra.db.mappers import event_from_orm, event_to_row_dict
from reflexor.infra.db.models import EventRow
from reflexor.infra.db.repos._common import _normalize_optional_str, _validate_limit_offset


class SqlAlchemyEventRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, event: Event) -> Event:
        row = EventRow(**event_to_row_dict(event))
        self._session.add(row)
        await self._session.flush()
        return event.model_copy(deep=True)

    async def get_by_dedupe(self, *, source: str, dedupe_key: str) -> Event | None:
        normalized_source = _normalize_optional_str(source)
        if normalized_source is None:
            raise ValueError("source must be non-empty")

        normalized_key = _normalize_optional_str(dedupe_key)
        if normalized_key is None:
            raise ValueError("dedupe_key must be non-empty")

        stmt: Select[tuple[EventRow]] = (
            select(EventRow)
            .where(EventRow.source == normalized_source, EventRow.dedupe_key == normalized_key)
            .order_by(EventRow.received_at_ms, EventRow.event_id)
            .limit(1)
        )
        result = await self._session.execute(stmt)
        row = result.scalars().one_or_none()
        if row is None:
            return None
        return event_from_orm(row)

    async def create_or_get_by_dedupe(
        self,
        *,
        source: str,
        dedupe_key: str,
        event: Event,
    ) -> tuple[Event, bool]:
        normalized_source = _normalize_optional_str(source)
        if normalized_source is None:
            raise ValueError("source must be non-empty")

        normalized_key = _normalize_optional_str(dedupe_key)
        if normalized_key is None:
            raise ValueError("dedupe_key must be non-empty")

        stmt: Select[tuple[EventRow]] = (
            select(EventRow)
            .where(EventRow.source == normalized_source, EventRow.dedupe_key == normalized_key)
            .order_by(EventRow.received_at_ms, EventRow.event_id)
            .limit(1)
        )
        result = await self._session.execute(stmt)
        row = result.scalars().one_or_none()
        if row is not None:
            return event_from_orm(row), False

        event_to_store = event.model_copy(
            update={"source": normalized_source, "dedupe_key": normalized_key},
            deep=True,
        )

        integrity_error: IntegrityError | None = None
        async with self._session.begin_nested() as nested:
            self._session.add(EventRow(**event_to_row_dict(event_to_store)))
            try:
                await self._session.flush()
            except IntegrityError as exc:
                integrity_error = exc
                await nested.rollback()
            else:
                return event_to_store.model_copy(deep=True), True

        result = await self._session.execute(stmt)
        row = result.scalars().one_or_none()
        if row is not None:
            return event_from_orm(row), False

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

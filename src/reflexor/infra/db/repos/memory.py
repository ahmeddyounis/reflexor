from __future__ import annotations

from sqlalchemy import Select, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from reflexor.infra.db.mappers import memory_item_from_orm, memory_item_to_row_dict
from reflexor.infra.db.models import MemoryItemRow, RunRow
from reflexor.infra.db.repos._common import _normalize_optional_str, _validate_limit_offset
from reflexor.memory.models import MemoryItem


class SqlAlchemyMemoryRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(self, item: MemoryItem) -> MemoryItem:
        run = await self._session.get(RunRow, item.run_id)
        if run is None:
            raise KeyError(f"unknown run_id: {item.run_id!r}")

        existing_stmt: Select[tuple[MemoryItemRow]] = select(MemoryItemRow).where(
            MemoryItemRow.run_id == item.run_id
        )
        existing = (await self._session.execute(existing_stmt)).scalar_one_or_none()

        row_dict = memory_item_to_row_dict(item)
        if existing is None:
            self._session.add(MemoryItemRow(**row_dict))
        else:
            replacement = MemoryItemRow(**row_dict)
            existing.memory_id = replacement.memory_id
            existing.event_id = replacement.event_id
            existing.kind = replacement.kind
            existing.event_type = replacement.event_type
            existing.event_source = replacement.event_source
            existing.summary = replacement.summary
            existing.content = replacement.content
            existing.tags = replacement.tags
            existing.created_at_ms = replacement.created_at_ms
            existing.updated_at_ms = replacement.updated_at_ms
        await self._session.flush()
        refreshed = (await self._session.execute(existing_stmt)).scalar_one()
        return memory_item_from_orm(refreshed)

    async def get_by_run(self, run_id: str) -> MemoryItem | None:
        normalized = _normalize_optional_str(run_id)
        if normalized is None:
            raise ValueError("run_id must be non-empty")

        stmt: Select[tuple[MemoryItemRow]] = select(MemoryItemRow).where(
            MemoryItemRow.run_id == normalized
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        return memory_item_from_orm(row)

    async def list_recent(
        self,
        *,
        limit: int,
        offset: int = 0,
        event_type: str | None = None,
        event_source: str | None = None,
    ) -> list[MemoryItem]:
        limit_int, offset_int = _validate_limit_offset(limit=limit, offset=offset)
        if limit_int == 0:
            return []

        normalized_event_type = _normalize_optional_str(event_type)
        normalized_event_source = _normalize_optional_str(event_source)

        stmt: Select[tuple[MemoryItemRow]] = select(MemoryItemRow)
        if normalized_event_type is not None:
            stmt = stmt.where(MemoryItemRow.event_type == normalized_event_type)
        if normalized_event_source is not None:
            stmt = stmt.where(MemoryItemRow.event_source == normalized_event_source)

        stmt = (
            stmt.order_by(MemoryItemRow.updated_at_ms.desc(), MemoryItemRow.memory_id.desc())
            .limit(limit_int)
            .offset(offset_int)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [memory_item_from_orm(row) for row in rows]

    async def search(
        self,
        *,
        query: str,
        limit: int,
        offset: int = 0,
        event_type: str | None = None,
        event_source: str | None = None,
    ) -> list[MemoryItem]:
        normalized_query = _normalize_optional_str(query)
        if normalized_query is None:
            raise ValueError("query must be non-empty")

        limit_int, offset_int = _validate_limit_offset(limit=limit, offset=offset)
        if limit_int == 0:
            return []

        normalized_event_type = _normalize_optional_str(event_type)
        normalized_event_source = _normalize_optional_str(event_source)

        stmt: Select[tuple[MemoryItemRow]] = select(MemoryItemRow).where(
            func.lower(MemoryItemRow.summary).like(f"%{normalized_query.lower()}%")
        )
        if normalized_event_type is not None:
            stmt = stmt.where(MemoryItemRow.event_type == normalized_event_type)
        if normalized_event_source is not None:
            stmt = stmt.where(MemoryItemRow.event_source == normalized_event_source)

        stmt = (
            stmt.order_by(MemoryItemRow.updated_at_ms.desc(), MemoryItemRow.memory_id.desc())
            .limit(limit_int)
            .offset(offset_int)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [memory_item_from_orm(row) for row in rows]

    async def delete_older_than(self, *, updated_before_ms: int, limit: int) -> int:
        limit_int, _ = _validate_limit_offset(limit=limit, offset=0)
        if limit_int == 0:
            return 0

        stmt = (
            select(MemoryItemRow.memory_id)
            .where(MemoryItemRow.updated_at_ms < int(updated_before_ms))
            .order_by(MemoryItemRow.updated_at_ms, MemoryItemRow.memory_id)
            .limit(limit_int)
        )
        memory_ids = list((await self._session.execute(stmt)).scalars().all())
        if not memory_ids:
            return 0

        await self._session.execute(
            delete(MemoryItemRow).where(MemoryItemRow.memory_id.in_(memory_ids))
        )
        await self._session.flush()
        return len(memory_ids)


__all__ = ["SqlAlchemyMemoryRepo"]

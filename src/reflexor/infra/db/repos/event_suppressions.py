from __future__ import annotations

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from reflexor.infra.db.models import EventSuppressionRow
from reflexor.infra.db.repos._common import _normalize_optional_str, _validate_limit_offset
from reflexor.storage.ports import EventSuppressionRecord


def _event_suppression_from_row(row: EventSuppressionRow) -> EventSuppressionRecord:
    return EventSuppressionRecord(
        signature_hash=row.signature_hash,
        event_type=row.event_type,
        event_source=row.event_source,
        signature=row.signature,
        window_start_ms=row.window_start_ms,
        count=row.count,
        threshold=row.threshold,
        window_ms=row.window_ms,
        suppressed_until_ms=row.suppressed_until_ms,
        resume_required=bool(row.resume_required),
        cleared_at_ms=row.cleared_at_ms,
        cleared_by=row.cleared_by,
        cleared_request_id=row.cleared_request_id,
        created_at_ms=row.created_at_ms,
        updated_at_ms=row.updated_at_ms,
        expires_at_ms=row.expires_at_ms,
    )


def _validated_record(record: EventSuppressionRecord) -> EventSuppressionRecord:
    normalized_hash = record.signature_hash.strip()
    if not normalized_hash:
        raise ValueError("signature_hash must be non-empty")

    normalized_type = record.event_type.strip()
    if not normalized_type:
        raise ValueError("event_type must be non-empty")

    normalized_source = record.event_source.strip()
    if not normalized_source:
        raise ValueError("event_source must be non-empty")

    if int(record.count) < 0:
        raise ValueError("count must be >= 0")
    if int(record.threshold) <= 0:
        raise ValueError("threshold must be > 0")
    if int(record.window_ms) <= 0:
        raise ValueError("window_ms must be > 0")
    if int(record.window_start_ms) < 0:
        raise ValueError("window_start_ms must be >= 0")
    if int(record.created_at_ms) < 0:
        raise ValueError("created_at_ms must be >= 0")
    if int(record.updated_at_ms) < int(record.created_at_ms):
        raise ValueError("updated_at_ms must be >= created_at_ms")
    if int(record.expires_at_ms) < int(record.updated_at_ms):
        raise ValueError("expires_at_ms must be >= updated_at_ms")

    suppressed_until_ms = (
        None if record.suppressed_until_ms is None else int(record.suppressed_until_ms)
    )
    if suppressed_until_ms is not None:
        if suppressed_until_ms < int(record.updated_at_ms):
            raise ValueError("suppressed_until_ms must be >= updated_at_ms")
        if int(record.expires_at_ms) < suppressed_until_ms:
            raise ValueError("expires_at_ms must be >= suppressed_until_ms")

    cleared_at_ms = None if record.cleared_at_ms is None else int(record.cleared_at_ms)
    if cleared_at_ms is not None:
        if cleared_at_ms < int(record.created_at_ms):
            raise ValueError("cleared_at_ms must be >= created_at_ms")
        if cleared_at_ms > int(record.updated_at_ms):
            raise ValueError("cleared_at_ms must be <= updated_at_ms")

    if not isinstance(record.signature, dict):
        raise ValueError("signature must be a JSON object")

    return EventSuppressionRecord(
        signature_hash=normalized_hash,
        event_type=normalized_type,
        event_source=normalized_source,
        signature=record.signature,
        window_start_ms=int(record.window_start_ms),
        count=int(record.count),
        threshold=int(record.threshold),
        window_ms=int(record.window_ms),
        suppressed_until_ms=suppressed_until_ms,
        resume_required=bool(record.resume_required),
        cleared_at_ms=cleared_at_ms,
        cleared_by=_normalize_optional_str(record.cleared_by),
        cleared_request_id=_normalize_optional_str(record.cleared_request_id),
        created_at_ms=int(record.created_at_ms),
        updated_at_ms=int(record.updated_at_ms),
        expires_at_ms=int(record.expires_at_ms),
    )


class SqlAlchemyEventSuppressionRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, signature_hash: str) -> EventSuppressionRecord | None:
        normalized = signature_hash.strip()
        if not normalized:
            raise ValueError("signature_hash must be non-empty")

        row = await self._session.get(EventSuppressionRow, normalized)
        if row is None:
            return None
        return _event_suppression_from_row(row)

    async def upsert(self, record: EventSuppressionRecord) -> EventSuppressionRecord:
        validated = _validated_record(record)
        normalized_hash = validated.signature_hash
        normalized_type = validated.event_type
        normalized_source = validated.event_source

        row = await self._session.get(EventSuppressionRow, normalized_hash)
        if row is not None:
            row.event_type = normalized_type
            row.event_source = normalized_source
            row.signature = validated.signature
            row.window_start_ms = validated.window_start_ms
            row.count = validated.count
            row.threshold = validated.threshold
            row.window_ms = validated.window_ms
            row.suppressed_until_ms = (
                validated.suppressed_until_ms
            )
            row.resume_required = validated.resume_required
            row.cleared_at_ms = validated.cleared_at_ms
            row.cleared_by = validated.cleared_by
            row.cleared_request_id = validated.cleared_request_id
            row.created_at_ms = validated.created_at_ms
            row.updated_at_ms = validated.updated_at_ms
            row.expires_at_ms = validated.expires_at_ms
            await self._session.flush()
            return _event_suppression_from_row(row)

        self._session.add(
            EventSuppressionRow(
                signature_hash=normalized_hash,
                event_type=normalized_type,
                event_source=normalized_source,
                signature=validated.signature,
                window_start_ms=validated.window_start_ms,
                count=validated.count,
                threshold=validated.threshold,
                window_ms=validated.window_ms,
                suppressed_until_ms=validated.suppressed_until_ms,
                resume_required=validated.resume_required,
                cleared_at_ms=validated.cleared_at_ms,
                cleared_by=validated.cleared_by,
                cleared_request_id=validated.cleared_request_id,
                created_at_ms=validated.created_at_ms,
                updated_at_ms=validated.updated_at_ms,
                expires_at_ms=validated.expires_at_ms,
            )
        )
        await self._session.flush()
        row = await self._session.get(EventSuppressionRow, normalized_hash)
        if row is None:  # pragma: no cover
            raise RuntimeError("failed to load event suppression after insert")
        return _event_suppression_from_row(row)

    async def delete(self, signature_hash: str) -> None:
        normalized = signature_hash.strip()
        if not normalized:
            raise ValueError("signature_hash must be non-empty")

        row = await self._session.get(EventSuppressionRow, normalized)
        if row is None:
            return
        await self._session.delete(row)
        await self._session.flush()

    async def count_active(self, *, now_ms: int) -> int:
        now_int = int(now_ms)
        stmt = (
            select(func.count())
            .select_from(EventSuppressionRow)
            .where(
                EventSuppressionRow.suppressed_until_ms.is_not(None),
                EventSuppressionRow.suppressed_until_ms > now_int,
                EventSuppressionRow.expires_at_ms > now_int,
            )
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one())

    async def list_active(
        self,
        *,
        now_ms: int,
        limit: int,
        offset: int,
    ) -> list[EventSuppressionRecord]:
        limit_int, offset_int = _validate_limit_offset(limit=limit, offset=offset)
        if limit_int == 0:
            return []

        now_int = int(now_ms)
        stmt: Select[tuple[EventSuppressionRow]] = (
            select(EventSuppressionRow)
            .where(
                EventSuppressionRow.suppressed_until_ms.is_not(None),
                EventSuppressionRow.suppressed_until_ms > now_int,
                EventSuppressionRow.expires_at_ms > now_int,
            )
            .order_by(
                EventSuppressionRow.suppressed_until_ms.desc(),
                EventSuppressionRow.signature_hash,
            )
            .limit(limit_int)
            .offset(offset_int)
        )
        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [_event_suppression_from_row(row) for row in rows]

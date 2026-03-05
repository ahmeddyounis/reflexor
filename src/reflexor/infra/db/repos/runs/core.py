from __future__ import annotations

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from reflexor.domain.enums import RunStatus
from reflexor.infra.db.models import RunRow
from reflexor.infra.db.repos._common import _validate_limit_offset
from reflexor.infra.db.repos.runs.summaries import (
    count_run_summaries,
    get_run_summary,
    list_run_summaries,
)
from reflexor.storage.ports import RunRecord, RunSummary


def _run_record_from_row(row: RunRow) -> RunRecord:
    return RunRecord(
        run_id=row.run_id,
        parent_run_id=row.parent_run_id,
        created_at_ms=row.created_at_ms,
        started_at_ms=row.started_at_ms,
        completed_at_ms=row.completed_at_ms,
    )


class SqlAlchemyRunRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, run: RunRecord) -> RunRecord:
        row = RunRow(
            run_id=run.run_id,
            parent_run_id=run.parent_run_id,
            created_at_ms=run.created_at_ms,
            started_at_ms=run.started_at_ms,
            completed_at_ms=run.completed_at_ms,
        )
        self._session.add(row)
        await self._session.flush()
        return run

    async def get(self, run_id: str) -> RunRecord | None:
        normalized = run_id.strip()
        if not normalized:
            raise ValueError("run_id must be non-empty")

        row = await self._session.get(RunRow, normalized)
        if row is None:
            return None
        return _run_record_from_row(row)

    async def update_timestamps(
        self,
        run_id: str,
        *,
        started_at_ms: int | None = None,
        completed_at_ms: int | None = None,
    ) -> RunRecord:
        normalized = run_id.strip()
        if not normalized:
            raise ValueError("run_id must be non-empty")

        row = await self._session.get(RunRow, normalized)
        if row is None:
            raise KeyError(f"unknown run_id: {normalized!r}")

        if started_at_ms is not None:
            row.started_at_ms = int(started_at_ms)
        if completed_at_ms is not None:
            row.completed_at_ms = int(completed_at_ms)

        await self._session.flush()
        return _run_record_from_row(row)

    async def list_recent(self, *, limit: int, offset: int) -> list[RunRecord]:
        limit_int, offset_int = _validate_limit_offset(limit=limit, offset=offset)
        if limit_int == 0:
            return []

        stmt: Select[tuple[RunRow]] = (
            select(RunRow)
            .order_by(RunRow.created_at_ms.desc(), RunRow.run_id.desc())
            .limit(limit_int)
            .offset(offset_int)
        )
        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [_run_record_from_row(row) for row in rows]

    async def list_summaries(
        self,
        *,
        limit: int,
        offset: int,
        status: RunStatus | None = None,
        created_after_ms: int | None = None,
        created_before_ms: int | None = None,
    ) -> list[RunSummary]:
        return await list_run_summaries(
            self._session,
            limit=limit,
            offset=offset,
            status=status,
            created_after_ms=created_after_ms,
            created_before_ms=created_before_ms,
        )

    async def count_summaries(
        self,
        *,
        status: RunStatus | None = None,
        created_after_ms: int | None = None,
        created_before_ms: int | None = None,
    ) -> int:
        return await count_run_summaries(
            self._session,
            status=status,
            created_after_ms=created_after_ms,
            created_before_ms=created_before_ms,
        )

    async def get_summary(self, run_id: str) -> RunSummary | None:
        return await get_run_summary(self._session, run_id)

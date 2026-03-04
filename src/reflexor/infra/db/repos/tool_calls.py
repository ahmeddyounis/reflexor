from __future__ import annotations

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from reflexor.domain.enums import ToolCallStatus
from reflexor.domain.models import ToolCall
from reflexor.infra.db.mappers import tool_call_from_orm, tool_call_to_row_dict
from reflexor.infra.db.models import ToolCallRow
from reflexor.infra.db.repos._common import _validate_limit_offset


class SqlAlchemyToolCallRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, tool_call: ToolCall) -> ToolCall:
        row = ToolCallRow(**tool_call_to_row_dict(tool_call))
        self._session.add(row)
        await self._session.flush()
        return tool_call.model_copy(deep=True)

    async def get(self, tool_call_id: str) -> ToolCall | None:
        normalized = tool_call_id.strip()
        if not normalized:
            raise ValueError("tool_call_id must be non-empty")

        row = await self._session.get(ToolCallRow, normalized)
        if row is None:
            return None
        return tool_call_from_orm(row)

    async def get_by_idempotency_key(self, idempotency_key: str) -> ToolCall | None:
        normalized = idempotency_key.strip()
        if not normalized:
            raise ValueError("idempotency_key must be non-empty")

        stmt: Select[tuple[ToolCallRow]] = (
            select(ToolCallRow)
            .where(ToolCallRow.idempotency_key == normalized)
            .order_by(ToolCallRow.created_at_ms, ToolCallRow.tool_call_id)
            .limit(1)
        )
        result = await self._session.execute(stmt)
        row = result.scalars().one_or_none()
        if row is None:
            return None
        return tool_call_from_orm(row)

    async def update_status(self, tool_call_id: str, status: ToolCallStatus) -> ToolCall:
        normalized = tool_call_id.strip()
        if not normalized:
            raise ValueError("tool_call_id must be non-empty")

        row = await self._session.get(ToolCallRow, normalized)
        if row is None:
            raise KeyError(f"unknown tool_call_id: {normalized!r}")

        row.status = status.value
        await self._session.flush()
        return tool_call_from_orm(row)

    async def update(self, tool_call: ToolCall) -> ToolCall:
        normalized = tool_call.tool_call_id.strip()
        if not normalized:
            raise ValueError("tool_call_id must be non-empty")

        row = await self._session.get(ToolCallRow, normalized)
        if row is None:
            raise KeyError(f"unknown tool_call_id: {normalized!r}")

        row.status = tool_call.status.value
        row.started_at_ms = tool_call.started_at_ms
        row.completed_at_ms = tool_call.completed_at_ms
        row.result_ref = tool_call.result_ref
        await self._session.flush()
        return tool_call_from_orm(row)

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        status: ToolCallStatus | None = None,
    ) -> list[ToolCall]:
        limit_int, offset_int = _validate_limit_offset(limit=limit, offset=offset)
        if limit_int == 0:
            return []

        stmt: Select[tuple[ToolCallRow]] = select(ToolCallRow)
        if status is not None:
            stmt = stmt.where(ToolCallRow.status == status.value)

        stmt = (
            stmt.order_by(ToolCallRow.created_at_ms, ToolCallRow.tool_call_id)
            .limit(limit_int)
            .offset(offset_int)
        )
        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [tool_call_from_orm(row) for row in rows]

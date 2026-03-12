from __future__ import annotations

import time

from sqlalchemy import Select, case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from reflexor.domain.enums import ApprovalStatus
from reflexor.domain.models import Approval
from reflexor.infra.db.mappers import approval_from_orm, approval_to_row_dict
from reflexor.infra.db.models import ApprovalRow, RunRow, TaskRow, ToolCallRow
from reflexor.infra.db.repos._common import _normalize_optional_str, _validate_limit_offset


def _approval_status_is_supported(status: ApprovalStatus) -> bool:
    return status in {ApprovalStatus.PENDING, ApprovalStatus.APPROVED, ApprovalStatus.DENIED}


class SqlAlchemyApprovalRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, approval: Approval) -> Approval:
        for table, pk, name in (
            (RunRow, approval.run_id, "run_id"),
            (TaskRow, approval.task_id, "task_id"),
            (ToolCallRow, approval.tool_call_id, "tool_call_id"),
        ):
            existing = await self._session.get(table, pk)
            if existing is None:
                raise KeyError(f"unknown {name}: {pk!r}")

        row = ApprovalRow(**approval_to_row_dict(approval))
        self._session.add(row)
        await self._session.flush()
        return approval.model_copy(deep=True)

    async def get(self, approval_id: str) -> Approval | None:
        normalized = approval_id.strip()
        if not normalized:
            raise ValueError("approval_id must be non-empty")

        row = await self._session.get(ApprovalRow, normalized)
        if row is None:
            return None
        return approval_from_orm(row)

    async def get_by_tool_call(self, tool_call_id: str) -> Approval | None:
        normalized = tool_call_id.strip()
        if not normalized:
            raise ValueError("tool_call_id must be non-empty")

        stmt: Select[tuple[ApprovalRow]] = (
            select(ApprovalRow)
            .where(ApprovalRow.tool_call_id == normalized)
            .order_by(ApprovalRow.created_at_ms, ApprovalRow.approval_id)
            .limit(1)
        )
        result = await self._session.execute(stmt)
        row = result.scalars().one_or_none()
        if row is None:
            return None
        return approval_from_orm(row)

    async def update_status(
        self,
        approval_id: str,
        status: ApprovalStatus,
        *,
        decided_at_ms: int | None = None,
        decided_by: str | None = None,
    ) -> Approval:
        if not _approval_status_is_supported(status):
            raise ValueError("unsupported approval status")

        normalized = approval_id.strip()
        if not normalized:
            raise ValueError("approval_id must be non-empty")

        if status == ApprovalStatus.PENDING:
            decided_at_value: int | None = None
            decided_by_value: str | None = None
        else:
            now_ms = int(time.time() * 1000)
            decided_at_value = now_ms if decided_at_ms is None else int(decided_at_ms)
            decided_by_value = _normalize_optional_str(decided_by)

        result = await self._session.execute(
            update(ApprovalRow)
            .where(
                ApprovalRow.approval_id == normalized,
                ApprovalRow.status == ApprovalStatus.PENDING.value,
            )
            .values(
                status=status.value,
                decided_at_ms=decided_at_value,
                decided_by=decided_by_value,
            )
        )
        updated_rows = int(getattr(result, "rowcount", 0) or 0)

        row = await self._session.get(ApprovalRow, normalized)
        if row is None:
            raise KeyError(f"unknown approval_id: {normalized!r}")

        if updated_rows == 0:
            current = approval_from_orm(row)
            if current.status == status:
                return current
            raise ValueError(f"approval has already been decided as {current.status.value}")

        await self._session.flush()
        return approval_from_orm(row)

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        status: ApprovalStatus | None = None,
        run_id: str | None = None,
    ) -> list[Approval]:
        limit_int, offset_int = _validate_limit_offset(limit=limit, offset=offset)
        if limit_int == 0:
            return []

        stmt: Select[tuple[ApprovalRow]] = select(ApprovalRow)
        if status is not None:
            if not _approval_status_is_supported(status):
                raise ValueError("unsupported approval status")
            stmt = stmt.where(ApprovalRow.status == status.value)
        if run_id is not None:
            normalized = _normalize_optional_str(run_id)
            if normalized is None:
                raise ValueError("run_id must be non-empty when provided")
            stmt = stmt.where(ApprovalRow.run_id == normalized)

        if status is None:
            pending_first = case((ApprovalRow.status == ApprovalStatus.PENDING.value, 0), else_=1)
            stmt = stmt.order_by(pending_first, ApprovalRow.created_at_ms, ApprovalRow.approval_id)
        else:
            stmt = stmt.order_by(ApprovalRow.created_at_ms, ApprovalRow.approval_id)

        stmt = stmt.limit(limit_int).offset(offset_int)
        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [approval_from_orm(row) for row in rows]

    async def count(
        self,
        *,
        status: ApprovalStatus | None = None,
        run_id: str | None = None,
    ) -> int:
        stmt = select(func.count(ApprovalRow.approval_id)).select_from(ApprovalRow)
        if status is not None:
            if not _approval_status_is_supported(status):
                raise ValueError("unsupported approval status")
            stmt = stmt.where(ApprovalRow.status == status.value)
        if run_id is not None:
            normalized = _normalize_optional_str(run_id)
            if normalized is None:
                raise ValueError("run_id must be non-empty when provided")
            stmt = stmt.where(ApprovalRow.run_id == normalized)

        result = await self._session.execute(stmt)
        return int(result.scalar_one())

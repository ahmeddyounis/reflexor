from __future__ import annotations

from typing import cast

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from reflexor.domain.enums import TaskStatus, ToolCallStatus
from reflexor.domain.models import Task
from reflexor.infra.db.mappers import (
    task_from_row_dict,
    task_to_row_dict,
    tool_call_to_row_dict,
)
from reflexor.infra.db.models import RunRow, TaskRow, ToolCallRow
from reflexor.infra.db.repos._common import _normalize_optional_str, _validate_limit_offset
from reflexor.storage.ports import TaskSummary


def _tool_call_row_to_dict(row: ToolCallRow) -> dict[str, object]:
    return {
        "tool_call_id": row.tool_call_id,
        "tool_name": row.tool_name,
        "args": row.args,
        "permission_scope": row.permission_scope,
        "idempotency_key": row.idempotency_key,
        "status": row.status,
        "created_at_ms": row.created_at_ms,
        "started_at_ms": row.started_at_ms,
        "completed_at_ms": row.completed_at_ms,
        "result_ref": row.result_ref,
    }


def _task_from_rows(task_row: TaskRow, tool_call_row: ToolCallRow | None) -> Task:
    tool_call_dict = None if tool_call_row is None else _tool_call_row_to_dict(tool_call_row)
    return task_from_row_dict(
        {
            "task_id": task_row.task_id,
            "run_id": task_row.run_id,
            "name": task_row.name,
            "status": task_row.status,
            "tool_call_id": task_row.tool_call_id,
            "attempts": task_row.attempts,
            "max_attempts": task_row.max_attempts,
            "timeout_s": task_row.timeout_s,
            "depends_on": task_row.depends_on,
            "created_at_ms": task_row.created_at_ms,
            "started_at_ms": task_row.started_at_ms,
            "completed_at_ms": task_row.completed_at_ms,
            "labels": task_row.labels,
            "metadata_json": task_row.metadata_json,
        },
        tool_call_row=tool_call_dict,
    )


def _task_summary_stmt(
    *,
    limit: int,
    offset: int,
    run_id: str | None = None,
    status: TaskStatus | None = None,
) -> Select[tuple[TaskRow, str | None, str | None, str | None, str | None, str | None]]:
    stmt = cast(
        Select[tuple[TaskRow, str | None, str | None, str | None, str | None, str | None]],
        select(
            TaskRow,
            ToolCallRow.tool_call_id,
            ToolCallRow.tool_name,
            ToolCallRow.permission_scope,
            ToolCallRow.idempotency_key,
            ToolCallRow.status,
        ).outerjoin(ToolCallRow, TaskRow.tool_call_id == ToolCallRow.tool_call_id),
    )
    if run_id is not None:
        normalized = _normalize_optional_str(run_id)
        if normalized is None:
            raise ValueError("run_id must be non-empty when provided")
        stmt = stmt.where(TaskRow.run_id == normalized)
    if status is not None:
        stmt = stmt.where(TaskRow.status == status.value)

    return stmt.order_by(TaskRow.created_at_ms.desc(), TaskRow.task_id.desc()).limit(
        int(limit)
    ).offset(int(offset))


class SqlAlchemyTaskRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, task: Task) -> Task:
        run = await self._session.get(RunRow, task.run_id)
        if run is None:
            raise KeyError(f"unknown run_id: {task.run_id!r}")

        if task.tool_call is not None:
            existing = await self._session.get(ToolCallRow, task.tool_call.tool_call_id)
            if existing is None:
                self._session.add(ToolCallRow(**tool_call_to_row_dict(task.tool_call)))
                await self._session.flush()

        row = TaskRow(**task_to_row_dict(task))
        self._session.add(row)
        await self._session.flush()
        return task.model_copy(deep=True)

    async def get(self, task_id: str) -> Task | None:
        normalized = task_id.strip()
        if not normalized:
            raise ValueError("task_id must be non-empty")

        stmt = cast(
            Select[tuple[TaskRow, ToolCallRow | None]],
            select(TaskRow, ToolCallRow)
            .outerjoin(ToolCallRow, TaskRow.tool_call_id == ToolCallRow.tool_call_id)
            .where(TaskRow.task_id == normalized),
        )
        result = await self._session.execute(stmt)
        row = result.one_or_none()
        if row is None:
            return None
        task_row, tool_call_row = row
        return _task_from_rows(task_row, tool_call_row)

    async def update_status(self, task_id: str, status: TaskStatus) -> Task:
        normalized = task_id.strip()
        if not normalized:
            raise ValueError("task_id must be non-empty")

        task_row = await self._session.get(TaskRow, normalized)
        if task_row is None:
            raise KeyError(f"unknown task_id: {normalized!r}")

        task_row.status = status.value
        await self._session.flush()

        tool_call_row = None
        if task_row.tool_call_id is not None:
            tool_call_row = await self._session.get(ToolCallRow, task_row.tool_call_id)
        return _task_from_rows(task_row, tool_call_row)

    async def update(self, task: Task) -> Task:
        normalized = task.task_id.strip()
        if not normalized:
            raise ValueError("task_id must be non-empty")

        task_row = await self._session.get(TaskRow, normalized)
        if task_row is None:
            raise KeyError(f"unknown task_id: {normalized!r}")

        task_row.status = task.status.value
        task_row.attempts = task.attempts
        task_row.started_at_ms = task.started_at_ms
        task_row.completed_at_ms = task.completed_at_ms
        await self._session.flush()

        tool_call_row = None
        if task_row.tool_call_id is not None:
            tool_call_row = await self._session.get(ToolCallRow, task_row.tool_call_id)
        return _task_from_rows(task_row, tool_call_row)

    async def list_by_run(self, run_id: str) -> list[Task]:
        normalized = _normalize_optional_str(run_id)
        if normalized is None:
            raise ValueError("run_id must be non-empty")

        stmt = cast(
            Select[tuple[TaskRow, ToolCallRow | None]],
            select(TaskRow, ToolCallRow)
            .outerjoin(ToolCallRow, TaskRow.tool_call_id == ToolCallRow.tool_call_id)
            .where(TaskRow.run_id == normalized)
            .order_by(TaskRow.created_at_ms, TaskRow.task_id),
        )
        result = await self._session.execute(stmt)
        rows = result.all()
        return [_task_from_rows(task_row, tool_call_row) for task_row, tool_call_row in rows]

    async def list_summaries(
        self,
        *,
        limit: int,
        offset: int,
        run_id: str | None = None,
        status: TaskStatus | None = None,
    ) -> list[TaskSummary]:
        limit_int, offset_int = _validate_limit_offset(limit=limit, offset=offset)
        if limit_int == 0:
            return []

        stmt = _task_summary_stmt(
            limit=limit_int,
            offset=offset_int,
            run_id=run_id,
            status=status,
        )
        result = await self._session.execute(stmt)
        rows = result.all()

        summaries: list[TaskSummary] = []
        for (
            task_row,
            tool_call_id,
            tool_name,
            permission_scope,
            idempotency_key,
            tool_call_status_value,
        ) in rows:
            tool_call_status = (
                None
                if tool_call_status_value is None
                else ToolCallStatus(str(tool_call_status_value))
            )

            summaries.append(
                TaskSummary(
                    task_id=task_row.task_id,
                    run_id=task_row.run_id,
                    name=task_row.name,
                    status=TaskStatus(str(task_row.status)),
                    attempts=int(task_row.attempts),
                    max_attempts=int(task_row.max_attempts),
                    timeout_s=int(task_row.timeout_s),
                    depends_on=list(task_row.depends_on),
                    created_at_ms=int(task_row.created_at_ms),
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    permission_scope=permission_scope,
                    idempotency_key=idempotency_key,
                    tool_call_status=tool_call_status,
                )
            )

        return summaries

    async def count_summaries(
        self,
        *,
        run_id: str | None = None,
        status: TaskStatus | None = None,
    ) -> int:
        stmt = select(func.count(TaskRow.task_id)).select_from(TaskRow)
        if run_id is not None:
            normalized = _normalize_optional_str(run_id)
            if normalized is None:
                raise ValueError("run_id must be non-empty when provided")
            stmt = stmt.where(TaskRow.run_id == normalized)
        if status is not None:
            stmt = stmt.where(TaskRow.status == status.value)

        result = await self._session.execute(stmt)
        return int(result.scalar_one())

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        run_id: str | None = None,
        status: TaskStatus | None = None,
    ) -> list[Task]:
        limit_int, offset_int = _validate_limit_offset(limit=limit, offset=offset)
        if limit_int == 0:
            return []

        stmt = cast(
            Select[tuple[TaskRow, ToolCallRow | None]],
            select(TaskRow, ToolCallRow).outerjoin(
                ToolCallRow, TaskRow.tool_call_id == ToolCallRow.tool_call_id
            ),
        )
        if run_id is not None:
            normalized = _normalize_optional_str(run_id)
            if normalized is None:
                raise ValueError("run_id must be non-empty when provided")
            stmt = stmt.where(TaskRow.run_id == normalized)
        if status is not None:
            stmt = stmt.where(TaskRow.status == status.value)

        stmt = (
            stmt.order_by(TaskRow.created_at_ms, TaskRow.task_id)
            .limit(limit_int)
            .offset(offset_int)
        )
        result = await self._session.execute(stmt)
        rows = result.all()
        return [_task_from_rows(task_row, tool_call_row) for task_row, tool_call_row in rows]

    async def archive_terminal_before(self, *, completed_before_ms: int, limit: int) -> int:
        limit_int, _ = _validate_limit_offset(limit=limit, offset=0)
        if limit_int == 0:
            return 0

        stmt = (
            select(TaskRow.task_id)
            .where(
                TaskRow.status.in_(
                    [
                        TaskStatus.SUCCEEDED.value,
                        TaskStatus.FAILED.value,
                        TaskStatus.CANCELED.value,
                    ]
                ),
                TaskRow.completed_at_ms.is_not(None),
                TaskRow.completed_at_ms < int(completed_before_ms),
            )
            .order_by(TaskRow.completed_at_ms, TaskRow.task_id)
            .limit(limit_int)
        )
        task_ids = list((await self._session.execute(stmt)).scalars().all())
        if not task_ids:
            return 0

        rows = (
            (await self._session.execute(select(TaskRow).where(TaskRow.task_id.in_(task_ids))))
            .scalars()
            .all()
        )
        for row in rows:
            row.status = TaskStatus.ARCHIVED.value
        await self._session.flush()
        return len(task_ids)

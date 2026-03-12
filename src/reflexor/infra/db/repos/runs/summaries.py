from __future__ import annotations

from typing import Any

from sqlalchemy import Select, case, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from reflexor.domain.enums import ApprovalStatus, RunStatus, TaskStatus
from reflexor.infra.db.models import ApprovalRow, RunPacketRow, RunRow, TaskRow
from reflexor.infra.db.repos._common import _validate_limit_offset
from reflexor.storage.ports import RunSummary


def _task_agg_subquery() -> Any:
    return (
        select(
            TaskRow.run_id.label("run_id"),
            func.count(TaskRow.task_id).label("tasks_total"),
            func.sum(case((TaskRow.status == TaskStatus.PENDING.value, 1), else_=0)).label(
                "tasks_pending"
            ),
            func.sum(case((TaskRow.status == TaskStatus.QUEUED.value, 1), else_=0)).label(
                "tasks_queued"
            ),
            func.sum(case((TaskRow.status == TaskStatus.RUNNING.value, 1), else_=0)).label(
                "tasks_running"
            ),
            func.sum(case((TaskRow.status == TaskStatus.SUCCEEDED.value, 1), else_=0)).label(
                "tasks_succeeded"
            ),
            func.sum(case((TaskRow.status == TaskStatus.FAILED.value, 1), else_=0)).label(
                "tasks_failed"
            ),
            func.sum(case((TaskRow.status == TaskStatus.CANCELED.value, 1), else_=0)).label(
                "tasks_canceled"
            ),
            func.sum(case((TaskRow.status == TaskStatus.ARCHIVED.value, 1), else_=0)).label(
                "tasks_archived"
            ),
        )
        .group_by(TaskRow.run_id)
        .subquery()
    )


def _approvals_agg_subquery() -> Any:
    return (
        select(
            ApprovalRow.run_id.label("run_id"),
            func.count(ApprovalRow.approval_id).label("approvals_total"),
            func.sum(case((ApprovalRow.status == ApprovalStatus.PENDING.value, 1), else_=0)).label(
                "approvals_pending"
            ),
        )
        .group_by(ApprovalRow.run_id)
        .subquery()
    )


def _computed_status_expr(
    *, task_agg: Any, label: str | None = "computed_status"
) -> tuple[Any, Any, Any, Any, Any, Any, Any, Any, Any]:
    tasks_total = func.coalesce(task_agg.c.tasks_total, 0)
    tasks_pending = func.coalesce(task_agg.c.tasks_pending, 0)
    tasks_queued = func.coalesce(task_agg.c.tasks_queued, 0)
    tasks_running = func.coalesce(task_agg.c.tasks_running, 0)
    tasks_succeeded = func.coalesce(task_agg.c.tasks_succeeded, 0)
    tasks_failed = func.coalesce(task_agg.c.tasks_failed, 0)
    tasks_canceled = func.coalesce(task_agg.c.tasks_canceled, 0)
    tasks_archived = func.coalesce(task_agg.c.tasks_archived, 0)
    pending_like = tasks_pending + tasks_queued
    terminal_success_like = tasks_succeeded + tasks_archived

    expr = case(
        (tasks_total == 0, literal(RunStatus.SUCCEEDED.value)),
        (tasks_archived == tasks_total, literal(RunStatus.ARCHIVED.value)),
        (tasks_failed > 0, literal(RunStatus.FAILED.value)),
        (tasks_canceled > 0, literal(RunStatus.CANCELED.value)),
        (tasks_running > 0, literal(RunStatus.RUNNING.value)),
        (terminal_success_like == tasks_total, literal(RunStatus.SUCCEEDED.value)),
        (terminal_success_like > 0, literal(RunStatus.RUNNING.value)),
        (pending_like > 0, literal(RunStatus.CREATED.value)),
        else_=literal(RunStatus.RUNNING.value),
    )
    if label is None:
        return (
            expr,
            tasks_total,
            tasks_pending,
            tasks_queued,
            tasks_running,
            tasks_succeeded,
            tasks_failed,
            tasks_canceled,
            tasks_archived,
        )
    return (
        expr.label(label),
        tasks_total,
        tasks_pending,
        tasks_queued,
        tasks_running,
        tasks_succeeded,
        tasks_failed,
        tasks_canceled,
        tasks_archived,
    )


def _event_field_expr(*, field: str, label: str) -> Any:
    # Project only the specific scalar fields needed for summaries instead of
    # materializing the full sanitized packet blob on every operator read.
    return func.nullif(func.trim(RunPacketRow.packet["event"][field].as_string()), "").label(label)


def _list_run_summaries_stmt(
    *,
    limit: int,
    offset: int,
    status: RunStatus | None = None,
    created_after_ms: int | None = None,
    created_before_ms: int | None = None,
) -> Any:
    task_agg = _task_agg_subquery()
    approvals_agg = _approvals_agg_subquery()

    (
        computed_status,
        tasks_total,
        tasks_pending,
        tasks_queued,
        tasks_running,
        tasks_succeeded,
        tasks_failed,
        tasks_canceled,
        _tasks_archived,
    ) = _computed_status_expr(task_agg=task_agg)

    approvals_total = func.coalesce(approvals_agg.c.approvals_total, 0)
    approvals_pending = func.coalesce(approvals_agg.c.approvals_pending, 0)
    event_type = _event_field_expr(field="type", label="event_type")
    event_source = _event_field_expr(field="source", label="event_source")

    stmt = (
        select(
            RunRow,
            event_type,
            event_source,
            computed_status,
            tasks_total.label("tasks_total"),
            tasks_pending.label("tasks_pending"),
            tasks_queued.label("tasks_queued"),
            tasks_running.label("tasks_running"),
            tasks_succeeded.label("tasks_succeeded"),
            tasks_failed.label("tasks_failed"),
            tasks_canceled.label("tasks_canceled"),
            approvals_total.label("approvals_total"),
            approvals_pending.label("approvals_pending"),
        )
        .outerjoin(task_agg, task_agg.c.run_id == RunRow.run_id)
        .outerjoin(approvals_agg, approvals_agg.c.run_id == RunRow.run_id)
        .outerjoin(RunPacketRow, RunPacketRow.run_id == RunRow.run_id)
    )

    if status is not None:
        stmt = stmt.where(computed_status == status.value)
    if created_after_ms is not None:
        stmt = stmt.where(RunRow.created_at_ms >= int(created_after_ms))
    if created_before_ms is not None:
        stmt = stmt.where(RunRow.created_at_ms <= int(created_before_ms))

    return (
        stmt.order_by(RunRow.created_at_ms.desc(), RunRow.run_id.desc())
        .limit(int(limit))
        .offset(int(offset))
    )


def _get_run_summary_stmt(run_id: str) -> Any:
    normalized = run_id.strip()
    if not normalized:
        raise ValueError("run_id must be non-empty")

    task_agg = _task_agg_subquery()
    approvals_agg = _approvals_agg_subquery()

    (
        computed_status,
        tasks_total,
        tasks_pending,
        tasks_queued,
        tasks_running,
        tasks_succeeded,
        tasks_failed,
        tasks_canceled,
        _tasks_archived,
    ) = _computed_status_expr(task_agg=task_agg)

    approvals_total = func.coalesce(approvals_agg.c.approvals_total, 0)
    approvals_pending = func.coalesce(approvals_agg.c.approvals_pending, 0)

    return (
        select(
            RunRow,
            _event_field_expr(field="type", label="event_type"),
            _event_field_expr(field="source", label="event_source"),
            computed_status,
            tasks_total.label("tasks_total"),
            tasks_pending.label("tasks_pending"),
            tasks_queued.label("tasks_queued"),
            tasks_running.label("tasks_running"),
            tasks_succeeded.label("tasks_succeeded"),
            tasks_failed.label("tasks_failed"),
            tasks_canceled.label("tasks_canceled"),
            approvals_total.label("approvals_total"),
            approvals_pending.label("approvals_pending"),
        )
        .outerjoin(task_agg, task_agg.c.run_id == RunRow.run_id)
        .outerjoin(approvals_agg, approvals_agg.c.run_id == RunRow.run_id)
        .outerjoin(RunPacketRow, RunPacketRow.run_id == RunRow.run_id)
        .where(RunRow.run_id == normalized)
        .limit(1)
    )


async def list_run_summaries(
    session: AsyncSession,
    *,
    limit: int,
    offset: int,
    status: RunStatus | None = None,
    created_after_ms: int | None = None,
    created_before_ms: int | None = None,
) -> list[RunSummary]:
    limit_int, offset_int = _validate_limit_offset(limit=limit, offset=offset)
    if limit_int == 0:
        return []

    stmt = _list_run_summaries_stmt(
        limit=limit_int,
        offset=offset_int,
        status=status,
        created_after_ms=created_after_ms,
        created_before_ms=created_before_ms,
    )
    result = await session.execute(stmt)
    rows = result.all()

    summaries: list[RunSummary] = []
    for (
        run_row,
        event_type_value,
        event_source_value,
        status_value,
        tasks_total_value,
        tasks_pending_value,
        tasks_queued_value,
        tasks_running_value,
        tasks_succeeded_value,
        tasks_failed_value,
        tasks_canceled_value,
        approvals_total_value,
        approvals_pending_value,
    ) in rows:
        summaries.append(
            RunSummary(
                run_id=run_row.run_id,
                created_at_ms=run_row.created_at_ms,
                started_at_ms=run_row.started_at_ms,
                completed_at_ms=run_row.completed_at_ms,
                status=RunStatus(str(status_value)),
                event_type=None if event_type_value is None else str(event_type_value),
                event_source=None if event_source_value is None else str(event_source_value),
                tasks_total=int(tasks_total_value),
                tasks_pending=int(tasks_pending_value),
                tasks_queued=int(tasks_queued_value),
                tasks_running=int(tasks_running_value),
                tasks_succeeded=int(tasks_succeeded_value),
                tasks_failed=int(tasks_failed_value),
                tasks_canceled=int(tasks_canceled_value),
                approvals_total=int(approvals_total_value),
                approvals_pending=int(approvals_pending_value),
            )
        )

    return summaries


async def count_run_summaries(
    session: AsyncSession,
    *,
    status: RunStatus | None = None,
    created_after_ms: int | None = None,
    created_before_ms: int | None = None,
) -> int:
    task_agg = _task_agg_subquery()
    computed_status, *_ = _computed_status_expr(task_agg=task_agg, label=None)

    stmt: Select[tuple[int]] = (
        select(func.count(RunRow.run_id))
        .select_from(RunRow)
        .outerjoin(task_agg, task_agg.c.run_id == RunRow.run_id)
    )

    if status is not None:
        stmt = stmt.where(computed_status == status.value)
    if created_after_ms is not None:
        stmt = stmt.where(RunRow.created_at_ms >= int(created_after_ms))
    if created_before_ms is not None:
        stmt = stmt.where(RunRow.created_at_ms <= int(created_before_ms))

    result = await session.execute(stmt)
    return int(result.scalar_one())


async def get_run_summary(session: AsyncSession, run_id: str) -> RunSummary | None:
    stmt = _get_run_summary_stmt(run_id)
    result = await session.execute(stmt)
    row = result.one_or_none()
    if row is None:
        return None

    (
        run_row,
        event_type_value,
        event_source_value,
        status_value,
        tasks_total_value,
        tasks_pending_value,
        tasks_queued_value,
        tasks_running_value,
        tasks_succeeded_value,
        tasks_failed_value,
        tasks_canceled_value,
        approvals_total_value,
        approvals_pending_value,
    ) = row

    return RunSummary(
        run_id=run_row.run_id,
        created_at_ms=run_row.created_at_ms,
        started_at_ms=run_row.started_at_ms,
        completed_at_ms=run_row.completed_at_ms,
        status=RunStatus(str(status_value)),
        event_type=None if event_type_value is None else str(event_type_value),
        event_source=None if event_source_value is None else str(event_source_value),
        tasks_total=int(tasks_total_value),
        tasks_pending=int(tasks_pending_value),
        tasks_queued=int(tasks_queued_value),
        tasks_running=int(tasks_running_value),
        tasks_succeeded=int(tasks_succeeded_value),
        tasks_failed=int(tasks_failed_value),
        tasks_canceled=int(tasks_canceled_value),
        approvals_total=int(approvals_total_value),
        approvals_pending=int(approvals_pending_value),
    )

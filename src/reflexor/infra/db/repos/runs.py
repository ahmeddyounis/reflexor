from __future__ import annotations

from sqlalchemy import Select, case, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from reflexor.domain.enums import ApprovalStatus, RunStatus, TaskStatus
from reflexor.infra.db.models import ApprovalRow, RunPacketRow, RunRow, TaskRow
from reflexor.infra.db.repos._common import _validate_limit_offset
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
        limit_int, offset_int = _validate_limit_offset(limit=limit, offset=offset)
        if limit_int == 0:
            return []

        task_agg = (
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
            )
            .group_by(TaskRow.run_id)
            .subquery()
        )

        approvals_agg = (
            select(
                ApprovalRow.run_id.label("run_id"),
                func.count(ApprovalRow.approval_id).label("approvals_total"),
                func.sum(
                    case((ApprovalRow.status == ApprovalStatus.PENDING.value, 1), else_=0)
                ).label("approvals_pending"),
            )
            .group_by(ApprovalRow.run_id)
            .subquery()
        )

        tasks_total = func.coalesce(task_agg.c.tasks_total, 0)
        tasks_pending = func.coalesce(task_agg.c.tasks_pending, 0)
        tasks_queued = func.coalesce(task_agg.c.tasks_queued, 0)
        tasks_running = func.coalesce(task_agg.c.tasks_running, 0)
        tasks_succeeded = func.coalesce(task_agg.c.tasks_succeeded, 0)
        tasks_failed = func.coalesce(task_agg.c.tasks_failed, 0)
        tasks_canceled = func.coalesce(task_agg.c.tasks_canceled, 0)
        pending_like = tasks_pending + tasks_queued

        computed_status = case(
            (tasks_total == 0, literal(RunStatus.SUCCEEDED.value)),
            (tasks_failed > 0, literal(RunStatus.FAILED.value)),
            (tasks_canceled > 0, literal(RunStatus.CANCELED.value)),
            (tasks_running > 0, literal(RunStatus.RUNNING.value)),
            (tasks_succeeded == tasks_total, literal(RunStatus.SUCCEEDED.value)),
            (tasks_succeeded > 0, literal(RunStatus.RUNNING.value)),
            (pending_like > 0, literal(RunStatus.CREATED.value)),
            else_=literal(RunStatus.RUNNING.value),
        ).label("computed_status")

        approvals_total = func.coalesce(approvals_agg.c.approvals_total, 0)
        approvals_pending = func.coalesce(approvals_agg.c.approvals_pending, 0)

        stmt = (
            select(
                RunRow,
                RunPacketRow.packet,
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

        stmt = (
            stmt.order_by(RunRow.created_at_ms.desc(), RunRow.run_id.desc())
            .limit(limit_int)
            .offset(offset_int)
        )
        result = await self._session.execute(stmt)
        rows = result.all()

        summaries: list[RunSummary] = []
        for (
            run_row,
            packet,
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
            event_type = None
            event_source = None
            if isinstance(packet, dict):
                event = packet.get("event")
                if isinstance(event, dict):
                    candidate_type = event.get("type")
                    if isinstance(candidate_type, str) and candidate_type.strip():
                        event_type = candidate_type
                    candidate_source = event.get("source")
                    if isinstance(candidate_source, str) and candidate_source.strip():
                        event_source = candidate_source

            summaries.append(
                RunSummary(
                    run_id=run_row.run_id,
                    created_at_ms=run_row.created_at_ms,
                    started_at_ms=run_row.started_at_ms,
                    completed_at_ms=run_row.completed_at_ms,
                    status=RunStatus(str(status_value)),
                    event_type=event_type,
                    event_source=event_source,
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

    async def count_summaries(
        self,
        *,
        status: RunStatus | None = None,
        created_after_ms: int | None = None,
        created_before_ms: int | None = None,
    ) -> int:
        task_agg = (
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
            )
            .group_by(TaskRow.run_id)
            .subquery()
        )

        approvals_agg = (
            select(
                ApprovalRow.run_id.label("run_id"),
                func.count(ApprovalRow.approval_id).label("approvals_total"),
                func.sum(
                    case((ApprovalRow.status == ApprovalStatus.PENDING.value, 1), else_=0)
                ).label("approvals_pending"),
            )
            .group_by(ApprovalRow.run_id)
            .subquery()
        )

        tasks_total = func.coalesce(task_agg.c.tasks_total, 0)
        tasks_pending = func.coalesce(task_agg.c.tasks_pending, 0)
        tasks_queued = func.coalesce(task_agg.c.tasks_queued, 0)
        tasks_running = func.coalesce(task_agg.c.tasks_running, 0)
        tasks_succeeded = func.coalesce(task_agg.c.tasks_succeeded, 0)
        tasks_failed = func.coalesce(task_agg.c.tasks_failed, 0)
        tasks_canceled = func.coalesce(task_agg.c.tasks_canceled, 0)
        pending_like = tasks_pending + tasks_queued

        computed_status = case(
            (tasks_total == 0, literal(RunStatus.SUCCEEDED.value)),
            (tasks_failed > 0, literal(RunStatus.FAILED.value)),
            (tasks_canceled > 0, literal(RunStatus.CANCELED.value)),
            (tasks_running > 0, literal(RunStatus.RUNNING.value)),
            (tasks_succeeded == tasks_total, literal(RunStatus.SUCCEEDED.value)),
            (tasks_succeeded > 0, literal(RunStatus.RUNNING.value)),
            (pending_like > 0, literal(RunStatus.CREATED.value)),
            else_=literal(RunStatus.RUNNING.value),
        )

        stmt = (
            select(func.count(RunRow.run_id))
            .select_from(RunRow)
            .outerjoin(task_agg, task_agg.c.run_id == RunRow.run_id)
            .outerjoin(approvals_agg, approvals_agg.c.run_id == RunRow.run_id)
        )

        if status is not None:
            stmt = stmt.where(computed_status == status.value)
        if created_after_ms is not None:
            stmt = stmt.where(RunRow.created_at_ms >= int(created_after_ms))
        if created_before_ms is not None:
            stmt = stmt.where(RunRow.created_at_ms <= int(created_before_ms))

        result = await self._session.execute(stmt)
        return int(result.scalar_one())

    async def get_summary(self, run_id: str) -> RunSummary | None:
        normalized = run_id.strip()
        if not normalized:
            raise ValueError("run_id must be non-empty")

        task_agg = (
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
            )
            .group_by(TaskRow.run_id)
            .subquery()
        )

        approvals_agg = (
            select(
                ApprovalRow.run_id.label("run_id"),
                func.count(ApprovalRow.approval_id).label("approvals_total"),
                func.sum(
                    case((ApprovalRow.status == ApprovalStatus.PENDING.value, 1), else_=0)
                ).label("approvals_pending"),
            )
            .group_by(ApprovalRow.run_id)
            .subquery()
        )

        tasks_total = func.coalesce(task_agg.c.tasks_total, 0)
        tasks_pending = func.coalesce(task_agg.c.tasks_pending, 0)
        tasks_queued = func.coalesce(task_agg.c.tasks_queued, 0)
        tasks_running = func.coalesce(task_agg.c.tasks_running, 0)
        tasks_succeeded = func.coalesce(task_agg.c.tasks_succeeded, 0)
        tasks_failed = func.coalesce(task_agg.c.tasks_failed, 0)
        tasks_canceled = func.coalesce(task_agg.c.tasks_canceled, 0)
        pending_like = tasks_pending + tasks_queued

        computed_status = case(
            (tasks_total == 0, literal(RunStatus.SUCCEEDED.value)),
            (tasks_failed > 0, literal(RunStatus.FAILED.value)),
            (tasks_canceled > 0, literal(RunStatus.CANCELED.value)),
            (tasks_running > 0, literal(RunStatus.RUNNING.value)),
            (tasks_succeeded == tasks_total, literal(RunStatus.SUCCEEDED.value)),
            (tasks_succeeded > 0, literal(RunStatus.RUNNING.value)),
            (pending_like > 0, literal(RunStatus.CREATED.value)),
            else_=literal(RunStatus.RUNNING.value),
        ).label("computed_status")

        approvals_total = func.coalesce(approvals_agg.c.approvals_total, 0)
        approvals_pending = func.coalesce(approvals_agg.c.approvals_pending, 0)

        stmt = (
            select(
                RunRow,
                RunPacketRow.packet,
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
        result = await self._session.execute(stmt)
        row = result.one_or_none()
        if row is None:
            return None

        (
            run_row,
            packet,
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

        event_type = None
        event_source = None
        if isinstance(packet, dict):
            event = packet.get("event")
            if isinstance(event, dict):
                candidate_type = event.get("type")
                if isinstance(candidate_type, str) and candidate_type.strip():
                    event_type = candidate_type
                candidate_source = event.get("source")
                if isinstance(candidate_source, str) and candidate_source.strip():
                    event_source = candidate_source

        return RunSummary(
            run_id=run_row.run_id,
            created_at_ms=run_row.created_at_ms,
            started_at_ms=run_row.started_at_ms,
            completed_at_ms=run_row.completed_at_ms,
            status=RunStatus(str(status_value)),
            event_type=event_type,
            event_source=event_source,
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

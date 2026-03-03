from __future__ import annotations

import time
from typing import cast

from sqlalchemy import Select, case, func, literal, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from reflexor.config import ReflexorSettings
from reflexor.domain.enums import ApprovalStatus, RunStatus, TaskStatus, ToolCallStatus
from reflexor.domain.models import Approval, Task, ToolCall
from reflexor.domain.models_event import Event
from reflexor.domain.models_run_packet import RunPacket
from reflexor.executor.idempotency import CachedOutcome, LedgerStatus, OutcomeToCache
from reflexor.infra.db.mappers import (
    approval_from_orm,
    approval_to_row_dict,
    event_from_orm,
    event_to_row_dict,
    task_from_row_dict,
    task_to_row_dict,
    tool_call_from_orm,
    tool_call_to_row_dict,
)
from reflexor.infra.db.models import (
    ApprovalRow,
    EventRow,
    IdempotencyLedgerRow,
    RunPacketRow,
    RunRow,
    TaskRow,
    ToolCallRow,
)
from reflexor.observability.audit_sanitize import sanitize_for_audit, sanitize_tool_output
from reflexor.storage.ports import RunRecord, RunSummary, TaskSummary
from reflexor.tools.sdk import ToolResult

RUN_PACKET_VERSION = 1


def _validate_limit_offset(*, limit: int, offset: int) -> tuple[int, int]:
    limit_int = int(limit)
    offset_int = int(offset)
    if limit_int < 0:
        raise ValueError("limit must be >= 0")
    if offset_int < 0:
        raise ValueError("offset must be >= 0")
    return limit_int, offset_int


def _normalize_optional_str(value: str | None) -> str | None:
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


def _run_record_from_row(row: RunRow) -> RunRecord:
    return RunRecord(
        run_id=row.run_id,
        parent_run_id=row.parent_run_id,
        created_at_ms=row.created_at_ms,
        started_at_ms=row.started_at_ms,
        completed_at_ms=row.completed_at_ms,
    )


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


def _approval_status_is_supported(status: ApprovalStatus) -> bool:
    return status in {ApprovalStatus.PENDING, ApprovalStatus.APPROVED, ApprovalStatus.DENIED}


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
            stmt.order_by(TaskRow.created_at_ms.desc(), TaskRow.task_id.desc())
            .limit(limit_int)
            .offset(offset_int)
        )
        result = await self._session.execute(stmt)
        rows = result.all()

        summaries: list[TaskSummary] = []
        for task_row, tool_call_row in rows:
            tool_call_status = None
            tool_call_id = None
            tool_name = None
            permission_scope = None
            idempotency_key = None

            if tool_call_row is not None:
                tool_call_id = tool_call_row.tool_call_id
                tool_name = tool_call_row.tool_name
                permission_scope = tool_call_row.permission_scope
                idempotency_key = tool_call_row.idempotency_key
                tool_call_status = ToolCallStatus(str(tool_call_row.status))

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

        row = await self._session.get(ApprovalRow, normalized)
        if row is None:
            raise KeyError(f"unknown approval_id: {normalized!r}")

        row.status = status.value
        if status == ApprovalStatus.PENDING:
            row.decided_at_ms = None
            row.decided_by = None
        else:
            now_ms = int(time.time() * 1000)
            row.decided_at_ms = now_ms if decided_at_ms is None else int(decided_at_ms)
            row.decided_by = _normalize_optional_str(decided_by)

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


class SqlAlchemyRunPacketRepo:
    def __init__(self, session: AsyncSession, *, settings: ReflexorSettings | None = None) -> None:
        self._session = session
        self._settings = settings

    async def create(self, packet: RunPacket) -> RunPacket:
        run = await self._session.get(RunRow, packet.run_id)
        if run is None:
            raise KeyError(f"unknown run_id: {packet.run_id!r}")

        packet_dict = packet.model_dump(mode="json")
        sanitized_packet = sanitize_for_audit(packet_dict, settings=self._settings)
        sanitized_packet["run_id"] = packet.run_id
        sanitized_packet["created_at_ms"] = packet.created_at_ms
        sanitized_packet["packet_version"] = RUN_PACKET_VERSION

        existing = await self._session.get(RunPacketRow, packet.run_id)
        if existing is None:
            row = RunPacketRow(
                run_id=packet.run_id,
                packet_version=RUN_PACKET_VERSION,
                created_at_ms=packet.created_at_ms,
                packet=sanitized_packet,
            )
            self._session.add(row)
        else:
            existing.packet_version = RUN_PACKET_VERSION
            existing.created_at_ms = packet.created_at_ms
            existing.packet = sanitized_packet
        await self._session.flush()
        return RunPacket.model_validate(sanitized_packet)

    async def get(self, run_id: str) -> RunPacket | None:
        normalized = run_id.strip()
        if not normalized:
            raise ValueError("run_id must be non-empty")

        row = await self._session.get(RunPacketRow, normalized)
        if row is None:
            return None

        sanitized_packet = sanitize_for_audit(row.packet, settings=self._settings)
        sanitized_packet["run_id"] = row.run_id
        sanitized_packet["created_at_ms"] = row.created_at_ms
        sanitized_packet["packet_version"] = int(row.packet_version)
        return RunPacket.model_validate(sanitized_packet)

    async def list_recent(self, *, limit: int, offset: int) -> list[RunPacket]:
        limit_int, offset_int = _validate_limit_offset(limit=limit, offset=offset)
        if limit_int == 0:
            return []

        stmt: Select[tuple[RunPacketRow]] = (
            select(RunPacketRow)
            .order_by(RunPacketRow.created_at_ms.desc(), RunPacketRow.run_id.desc())
            .limit(limit_int)
            .offset(offset_int)
        )
        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        packets: list[RunPacket] = []
        for row in rows:
            sanitized_packet = sanitize_for_audit(row.packet, settings=self._settings)
            sanitized_packet["run_id"] = row.run_id
            sanitized_packet["created_at_ms"] = row.created_at_ms
            sanitized_packet["packet_version"] = int(row.packet_version)
            packets.append(RunPacket.model_validate(sanitized_packet))
        return packets

    async def get_run_id_for_event(self, event_id: str) -> str | None:
        normalized = event_id.strip()
        if not normalized:
            raise ValueError("event_id must be non-empty")

        stmt = (
            select(RunPacketRow.run_id)
            .where(RunPacketRow.packet["event"]["event_id"].as_string() == normalized)
            .order_by(RunPacketRow.created_at_ms, RunPacketRow.run_id)
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()


class SqlAlchemyIdempotencyLedger:
    """SQLAlchemy-backed adapter for the executor IdempotencyLedger port."""

    def __init__(self, session: AsyncSession, *, settings: ReflexorSettings | None = None) -> None:
        self._session = session
        self._settings = settings

    async def get_success(self, key: str) -> CachedOutcome | None:
        normalized = key.strip()
        if not normalized:
            raise ValueError("key must be non-empty")

        row = await self._session.get(IdempotencyLedgerRow, normalized)
        if row is None:
            return None

        if row.status != LedgerStatus.SUCCEEDED.value:
            return None

        now_ms = int(time.time() * 1000)
        if row.expires_at_ms is not None and row.expires_at_ms <= now_ms:
            return None

        cached_result = ToolResult.model_validate(row.result_json)
        return CachedOutcome(
            idempotency_key=row.idempotency_key,
            tool_name=row.tool_name,
            status=LedgerStatus.SUCCEEDED,
            result=cached_result,
            created_at_ms=row.created_at_ms,
            updated_at_ms=row.updated_at_ms,
            expires_at_ms=row.expires_at_ms,
        )

    async def record_success(self, key: str, outcome: OutcomeToCache) -> None:
        if not outcome.result.ok:
            raise ValueError("record_success requires an ok ToolResult")
        await self._upsert(
            key=key,
            outcome=outcome,
            status=LedgerStatus.SUCCEEDED,
            allow_overwrite=True,
        )

    async def record_failure(self, key: str, outcome: OutcomeToCache, transient: bool) -> None:
        if outcome.result.ok:
            raise ValueError("record_failure requires ok=false ToolResult")

        status = LedgerStatus.FAILED_TRANSIENT if transient else LedgerStatus.FAILED_PERMANENT
        await self._upsert(
            key=key,
            outcome=outcome,
            status=status,
            allow_overwrite=False,
        )

    async def _upsert(
        self,
        *,
        key: str,
        outcome: OutcomeToCache,
        status: LedgerStatus,
        allow_overwrite: bool,
    ) -> None:
        normalized = key.strip()
        if not normalized:
            raise ValueError("key must be non-empty")

        now_ms = int(time.time() * 1000)

        result_payload = outcome.result.model_dump(mode="json")
        sanitized_result = sanitize_tool_output(result_payload, settings=self._settings)
        if not isinstance(sanitized_result, dict):
            raise ValueError("sanitized tool result must be a JSON object")

        row = await self._session.get(IdempotencyLedgerRow, normalized)
        if row is not None:
            if not allow_overwrite and row.status == LedgerStatus.SUCCEEDED.value:
                return
            row.tool_name = outcome.tool_name
            row.status = status.value
            row.result_json = sanitized_result
            row.updated_at_ms = now_ms
            row.expires_at_ms = outcome.expires_at_ms
            await self._session.flush()
            return

        integrity_error: IntegrityError | None = None
        async with self._session.begin_nested() as nested:
            self._session.add(
                IdempotencyLedgerRow(
                    idempotency_key=normalized,
                    tool_name=outcome.tool_name,
                    status=status.value,
                    result_json=sanitized_result,
                    created_at_ms=now_ms,
                    updated_at_ms=now_ms,
                    expires_at_ms=outcome.expires_at_ms,
                )
            )
            try:
                await self._session.flush()
            except IntegrityError as exc:
                integrity_error = exc
                await nested.rollback()
            else:
                return

        row = await self._session.get(IdempotencyLedgerRow, normalized)
        if row is not None:
            if not allow_overwrite and row.status == LedgerStatus.SUCCEEDED.value:
                return
            row.tool_name = outcome.tool_name
            row.status = status.value
            row.result_json = sanitized_result
            row.updated_at_ms = now_ms
            row.expires_at_ms = outcome.expires_at_ms
            await self._session.flush()
            return

        if integrity_error is not None:  # pragma: no cover
            raise integrity_error
        raise RuntimeError("failed to record outcome in idempotency ledger")


__all__ = [
    "SqlAlchemyApprovalRepo",
    "SqlAlchemyEventRepo",
    "SqlAlchemyIdempotencyLedger",
    "SqlAlchemyRunPacketRepo",
    "SqlAlchemyRunRepo",
    "SqlAlchemyTaskRepo",
    "SqlAlchemyToolCallRepo",
]

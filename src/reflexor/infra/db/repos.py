from __future__ import annotations

import time
from typing import cast

from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from reflexor.config import ReflexorSettings
from reflexor.domain.enums import ApprovalStatus, TaskStatus, ToolCallStatus
from reflexor.domain.models import Approval, Task, ToolCall
from reflexor.domain.models_event import Event
from reflexor.domain.models_run_packet import RunPacket
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
    RunPacketRow,
    RunRow,
    TaskRow,
    ToolCallRow,
)
from reflexor.observability.audit_sanitize import sanitize_for_audit
from reflexor.storage.ports import RunRecord

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
    ) -> list[Approval]:
        limit_int, offset_int = _validate_limit_offset(limit=limit, offset=offset)
        if limit_int == 0:
            return []

        stmt: Select[tuple[ApprovalRow]] = select(ApprovalRow)
        if status is not None:
            if not _approval_status_is_supported(status):
                raise ValueError("unsupported approval status")
            stmt = stmt.where(ApprovalRow.status == status.value)

        stmt = (
            stmt.order_by(ApprovalRow.created_at_ms, ApprovalRow.approval_id)
            .limit(limit_int)
            .offset(offset_int)
        )
        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [approval_from_orm(row) for row in rows]


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

        row = RunPacketRow(
            run_id=packet.run_id,
            packet_version=RUN_PACKET_VERSION,
            created_at_ms=packet.created_at_ms,
            packet=sanitized_packet,
        )
        self._session.add(row)
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


__all__ = [
    "SqlAlchemyApprovalRepo",
    "SqlAlchemyEventRepo",
    "SqlAlchemyRunPacketRepo",
    "SqlAlchemyRunRepo",
    "SqlAlchemyTaskRepo",
    "SqlAlchemyToolCallRepo",
]

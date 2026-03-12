from __future__ import annotations

from sqlalchemy import Select, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from reflexor.config import ReflexorSettings
from reflexor.domain.models_run_packet import RunPacket
from reflexor.infra.db.models import EventRow, MemoryItemRow, RunPacketRow, RunRow
from reflexor.infra.db.repos._common import _validate_limit_offset
from reflexor.memory import MEMORY_SUMMARY_VERSION, memory_item_from_run_packet
from reflexor.observability.audit_sanitize import sanitize_for_audit
from reflexor.storage.ports import MemoryRepo

RUN_PACKET_VERSION = 1


class SqlAlchemyRunPacketRepo:
    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: ReflexorSettings | None = None,
        memory_repo: MemoryRepo | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._memory_repo = memory_repo

    async def create(self, packet: RunPacket) -> RunPacket:
        run = await self._session.get(RunRow, packet.run_id)
        if run is None:
            raise KeyError(f"unknown run_id: {packet.run_id!r}")

        packet_dict = packet.model_dump(mode="json")
        sanitized_packet = sanitize_for_audit(packet_dict, settings=self._settings)
        sanitized_packet["run_id"] = packet.run_id
        sanitized_packet["created_at_ms"] = packet.created_at_ms
        sanitized_packet["packet_version"] = RUN_PACKET_VERSION
        stored_packet = RunPacket.model_validate(sanitized_packet)

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
        if self._memory_repo is not None:
            event_row = await self._session.get(EventRow, stored_packet.event.event_id)
            if event_row is None:
                raise KeyError(f"unknown event_id: {stored_packet.event.event_id!r}")
            memory_item = memory_item_from_run_packet(stored_packet)
            await self._memory_repo.upsert(memory_item)
        return stored_packet

    async def get(self, run_id: str) -> RunPacket | None:
        normalized = run_id.strip()
        if not normalized:
            raise ValueError("run_id must be non-empty")

        row = await self._session.get(RunPacketRow, normalized)
        if row is None:
            return None

        return self._row_to_packet(row)

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
        return [self._row_to_packet(row) for row in rows]

    async def list_before(
        self,
        *,
        created_before_ms: int,
        limit: int,
        offset: int = 0,
    ) -> list[RunPacket]:
        limit_int, offset_int = _validate_limit_offset(limit=limit, offset=offset)
        if limit_int == 0:
            return []

        stmt: Select[tuple[RunPacketRow]] = (
            select(RunPacketRow)
            .where(RunPacketRow.created_at_ms < int(created_before_ms))
            .order_by(RunPacketRow.created_at_ms, RunPacketRow.run_id)
            .limit(limit_int)
            .offset(offset_int)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [self._row_to_packet(row) for row in rows]

    async def list_for_memory_refresh_before(
        self,
        *,
        created_before_ms: int,
        memory_version: str = MEMORY_SUMMARY_VERSION,
        limit: int,
        offset: int = 0,
    ) -> list[RunPacket]:
        limit_int, offset_int = _validate_limit_offset(limit=limit, offset=offset)
        if limit_int == 0:
            return []

        normalized_version = memory_version.strip()
        if not normalized_version:
            raise ValueError("memory_version must be non-empty")

        stmt: Select[tuple[RunPacketRow]] = (
            select(RunPacketRow)
            .outerjoin(MemoryItemRow, RunPacketRow.run_id == MemoryItemRow.run_id)
            .where(
                RunPacketRow.created_at_ms < int(created_before_ms),
                or_(
                    MemoryItemRow.run_id.is_(None),
                    MemoryItemRow.content["memory_version"].as_string().is_(None),
                    MemoryItemRow.content["memory_version"].as_string() != normalized_version,
                ),
            )
            .order_by(RunPacketRow.created_at_ms, RunPacketRow.run_id)
            .limit(limit_int)
            .offset(offset_int)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [self._row_to_packet(row) for row in rows]

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

    def _row_to_packet(self, row: RunPacketRow) -> RunPacket:
        sanitized_packet = sanitize_for_audit(row.packet, settings=self._settings)
        sanitized_packet["run_id"] = row.run_id
        sanitized_packet["created_at_ms"] = row.created_at_ms
        sanitized_packet["packet_version"] = int(row.packet_version)
        return RunPacket.model_validate(sanitized_packet)

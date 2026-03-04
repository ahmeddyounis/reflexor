from __future__ import annotations

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from reflexor.config import ReflexorSettings
from reflexor.domain.models_run_packet import RunPacket
from reflexor.infra.db.models import RunPacketRow, RunRow
from reflexor.infra.db.repos._common import _validate_limit_offset
from reflexor.observability.audit_sanitize import sanitize_for_audit

RUN_PACKET_VERSION = 1


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

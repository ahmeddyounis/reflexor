"""RunPacket sinks (audit emission).

Run packets are produced by the orchestrator to support audit, debugging, and replay. Sinks are
application-layer adapters that accept `RunPacket` objects and emit them elsewhere.

Clean Architecture:
- Orchestrator is application-layer code.
- Sinks may depend on `reflexor.domain`, `reflexor.config`, and `reflexor.observability` utilities.
- Forbidden: FastAPI/SQLAlchemy/httpx/worker/API/CLI imports.
"""

from __future__ import annotations

import asyncio
from collections import deque
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Protocol

from reflexor.config import ReflexorSettings
from reflexor.domain.models_run_packet import RunPacket
from reflexor.observability.audit_sanitize import sanitize_for_audit


class RunPacketSink(Protocol):
    async def emit(self, packet: RunPacket) -> None: ...


class NoopRunPacketSink:
    async def emit(self, packet: RunPacket) -> None:
        _ = packet


@dataclass(slots=True)
class InMemoryRunPacketSink:
    """In-memory run packet sink that stores sanitized packets for tests/dev."""

    settings: ReflexorSettings | None = None
    max_items: int | None = None

    _packets_by_run_id: dict[str, dict[str, object]] = field(default_factory=dict, init=False)
    _order: deque[str] = field(default_factory=deque, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    async def emit(self, packet: RunPacket) -> None:
        raw = packet.model_dump(mode="json")
        sanitized = sanitize_for_audit(raw, settings=self.settings)

        run_id = packet.run_id
        run_id_from_packet = sanitized.get("run_id")
        if isinstance(run_id_from_packet, str) and run_id_from_packet.strip():
            run_id = run_id_from_packet

        async with self._lock:
            if run_id in self._packets_by_run_id:
                try:
                    self._order.remove(run_id)
                except ValueError:
                    pass

            self._packets_by_run_id[run_id] = deepcopy(sanitized)
            self._order.append(run_id)
            self._enforce_max_items()

    async def get(self, run_id: str) -> dict[str, object] | None:
        normalized = run_id.strip()
        if not normalized:
            raise ValueError("run_id must be non-empty")

        async with self._lock:
            packet = self._packets_by_run_id.get(normalized)
            return deepcopy(packet) if packet is not None else None

    async def list_recent(self, limit: int = 50) -> list[dict[str, object]]:
        if int(limit) <= 0:
            return []
        limit_int = int(limit)

        async with self._lock:
            run_ids = list(self._order)[-limit_int:]
            packets: list[dict[str, object]] = []
            for run_id in reversed(run_ids):
                packet = self._packets_by_run_id.get(run_id)
                if packet is None:
                    continue
                packets.append(deepcopy(packet))
            return packets

    def _enforce_max_items(self) -> None:
        max_items = self.max_items
        if max_items is None:
            return

        limit = int(max_items)
        if limit <= 0:
            self._packets_by_run_id.clear()
            self._order.clear()
            return

        while len(self._order) > limit:
            oldest_run_id = self._order.popleft()
            self._packets_by_run_id.pop(oldest_run_id, None)


__all__ = [
    "InMemoryRunPacketSink",
    "NoopRunPacketSink",
    "RunPacketSink",
]

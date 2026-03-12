from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from reflexor.config import ReflexorSettings
from reflexor.memory import memory_item_from_run_packet
from reflexor.memory.summary import MEMORY_SUMMARY_VERSION
from reflexor.orchestrator.clock import Clock
from reflexor.storage.ports import EventRepo, MemoryRepo, RunPacketRepo, TaskRepo
from reflexor.storage.uow import DatabaseSession, UnitOfWork

_MS_PER_DAY = 86_400_000


@dataclass(frozen=True, slots=True)
class MaintenanceOutcome:
    compacted_run_packets: int
    pruned_memory_items: int
    archived_tasks: int
    pruned_expired_dedupe_keys: int


@dataclass(frozen=True, slots=True)
class MaintenanceService:
    settings: ReflexorSettings
    clock: Clock
    uow_factory: Callable[[], UnitOfWork]
    event_repo: Callable[[DatabaseSession], EventRepo]
    run_packet_repo: Callable[[DatabaseSession], RunPacketRepo]
    memory_repo: Callable[[DatabaseSession], MemoryRepo]
    task_repo: Callable[[DatabaseSession], TaskRepo]

    async def run_once(self, *, now_ms: int | None = None) -> MaintenanceOutcome:
        effective_now_ms = int(self.clock.now_ms() if now_ms is None else now_ms)
        batch_size = int(self.settings.maintenance_batch_size)

        compacted_run_packets = await self._refresh_memory_from_run_packets(
            now_ms=effective_now_ms,
            limit=batch_size,
        )
        pruned_memory_items = await self._prune_memory(now_ms=effective_now_ms, limit=batch_size)
        archived_tasks = await self._archive_terminal_tasks(
            now_ms=effective_now_ms,
            limit=batch_size,
        )
        pruned_expired_dedupe_keys = await self._prune_expired_dedupe(
            now_ms=effective_now_ms,
            limit=batch_size,
        )

        return MaintenanceOutcome(
            compacted_run_packets=compacted_run_packets,
            pruned_memory_items=pruned_memory_items,
            archived_tasks=archived_tasks,
            pruned_expired_dedupe_keys=pruned_expired_dedupe_keys,
        )

    async def _refresh_memory_from_run_packets(self, *, now_ms: int, limit: int) -> int:
        compact_before_ms = now_ms - (int(self.settings.memory_compaction_after_days) * _MS_PER_DAY)
        uow = self.uow_factory()
        async with uow:
            run_packet_repo = self.run_packet_repo(uow.session)
            memory_repo = self.memory_repo(uow.session)
            packets = await run_packet_repo.list_for_memory_refresh_before(
                created_before_ms=compact_before_ms,
                memory_version=MEMORY_SUMMARY_VERSION,
                limit=limit,
                offset=0,
            )
            for packet in packets:
                await memory_repo.upsert(memory_item_from_run_packet(packet))
            return len(packets)

    async def _prune_memory(self, *, now_ms: int, limit: int) -> int:
        retention_days = self.settings.memory_retention_days
        if retention_days is None:
            return 0

        updated_before_ms = now_ms - (int(retention_days) * _MS_PER_DAY)
        uow = self.uow_factory()
        async with uow:
            repo = self.memory_repo(uow.session)
            return await repo.delete_older_than(updated_before_ms=updated_before_ms, limit=limit)

    async def _archive_terminal_tasks(self, *, now_ms: int, limit: int) -> int:
        archive_days = self.settings.archive_terminal_tasks_after_days
        if archive_days is None:
            return 0

        completed_before_ms = now_ms - (int(archive_days) * _MS_PER_DAY)
        uow = self.uow_factory()
        async with uow:
            repo = self.task_repo(uow.session)
            return await repo.archive_terminal_before(
                completed_before_ms=completed_before_ms,
                limit=limit,
            )

    async def _prune_expired_dedupe(self, *, now_ms: int, limit: int) -> int:
        uow = self.uow_factory()
        async with uow:
            repo = self.event_repo(uow.session)
            return await repo.prune_expired_dedupe(now_ms=now_ms, limit=limit)


__all__ = ["MaintenanceOutcome", "MaintenanceService"]

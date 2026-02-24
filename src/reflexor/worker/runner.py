"""Worker runner (process runtime).

This module hosts the long-running worker loop that:
- dequeues task envelopes from the queue
- runs executor logic for each lease
- supports graceful shutdown

Clean Architecture:
- Worker is an outer-layer runtime package. It may depend on the executor application layer and on
  infrastructure wiring for adapters.
- Forbidden: FastAPI/Starlette and CLI entrypoints.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Final

from reflexor.executor.service import ExecutorService
from reflexor.observability.context import correlation_context
from reflexor.orchestrator.queue import Lease, Queue, QueueClosed
from reflexor.worker.signals import install_shutdown_handlers

_DEFAULT_DEQUEUE_WAIT_S: Final[float] = 0.5


@dataclass(slots=True)
class WorkerRunner:
    """Background worker runner.

    The runner is intentionally small and DI-friendly. It does not wire adapters; composition roots
    should provide `queue` and `executor`.
    """

    queue: Queue
    executor: ExecutorService
    visibility_timeout_s: float = 60.0
    dequeue_wait_s: float | None = _DEFAULT_DEQUEUE_WAIT_S
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    install_signal_handlers: bool = True
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger(__name__))

    async def run(self) -> None:
        """Run the worker loop until `stop_event` is set or the queue is closed."""

        if self.visibility_timeout_s <= 0:
            raise ValueError("visibility_timeout_s must be > 0")
        if self.dequeue_wait_s is not None and self.dequeue_wait_s < 0:
            raise ValueError("dequeue_wait_s must be >= 0 when provided")

        if self.install_signal_handlers:
            install_shutdown_handlers(self.stop_event)

        stop_task = asyncio.create_task(self.stop_event.wait())
        try:
            while True:
                if self.stop_event.is_set():
                    break

                lease = await self._dequeue_or_stop(stop_task=stop_task)
                if lease is None:
                    if self.stop_event.is_set():
                        break
                    continue

                await self._handle_lease(lease)
        finally:
            stop_task.cancel()
            try:
                await stop_task
            except asyncio.CancelledError:
                pass

            await self.queue.aclose()

    def request_stop(self) -> None:
        self.stop_event.set()

    async def _dequeue_or_stop(self, *, stop_task: asyncio.Task[bool]) -> Lease | None:
        dequeue_task = asyncio.create_task(
            self.queue.dequeue(
                timeout_s=float(self.visibility_timeout_s),
                wait_s=self.dequeue_wait_s,
            )
        )
        done, pending = await asyncio.wait(
            {stop_task, dequeue_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        if dequeue_task in done:
            try:
                return dequeue_task.result()
            except QueueClosed:
                self.stop_event.set()
                return None

        dequeue_task.cancel()
        try:
            await dequeue_task
        except asyncio.CancelledError:
            pass
        return None

    async def _handle_lease(self, lease: Lease) -> None:
        correlation_ids = lease.envelope.correlation_ids or {}

        with correlation_context(
            event_id=correlation_ids.get("event_id"),
            run_id=correlation_ids.get("run_id") or lease.envelope.run_id,
            task_id=correlation_ids.get("task_id") or lease.envelope.task_id,
            tool_call_id=correlation_ids.get("tool_call_id"),
        ):
            try:
                await self.executor.process_lease(lease)
            except Exception:
                self.logger.exception(
                    "worker failed to process lease",
                    extra={
                        "envelope_id": lease.envelope.envelope_id,
                        "task_id": lease.envelope.task_id,
                        "run_id": lease.envelope.run_id,
                        "attempt": lease.attempt,
                    },
                )
                try:
                    await self.queue.nack(lease, delay_s=0.0, reason="worker_exception")
                except QueueClosed:
                    self.stop_event.set()


__all__ = ["WorkerRunner"]

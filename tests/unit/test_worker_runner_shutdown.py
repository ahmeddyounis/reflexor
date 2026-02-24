from __future__ import annotations

import asyncio

import pytest

from reflexor.infra.queue.in_memory_queue import InMemoryQueue
from reflexor.orchestrator.queue import Lease, QueueClosed
from reflexor.worker.runner import WorkerRunner


class _NoopExecutor:
    async def process_lease(self, lease: Lease) -> None:  # pragma: no cover
        _ = lease
        raise AssertionError("executor should not be called when the queue is empty")


async def test_worker_shutdown_unblocks_waiting_dequeue() -> None:
    queue = InMemoryQueue()
    stop_event = asyncio.Event()

    runner = WorkerRunner(
        queue=queue,
        executor=_NoopExecutor(),  # type: ignore[arg-type]
        stop_event=stop_event,
        dequeue_wait_s=None,
        install_signal_handlers=False,
    )

    task = asyncio.create_task(runner.run())
    await asyncio.sleep(0)

    stop_event.set()
    await asyncio.wait_for(task, timeout=0.5)

    with pytest.raises(QueueClosed, match="queue is closed"):
        await queue.dequeue()

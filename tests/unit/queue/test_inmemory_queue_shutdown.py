from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from reflexor.infra.queue.in_memory_queue import InMemoryQueue
from reflexor.orchestrator.queue import QueueClosed, TaskEnvelope


def _envelope() -> TaskEnvelope:
    return TaskEnvelope(
        envelope_id=str(uuid4()),
        task_id=str(uuid4()),
        run_id=str(uuid4()),
        attempt=0,
        created_at_ms=0,
        available_at_ms=0,
    )


async def test_aclose_is_idempotent_and_prevents_further_enqueue_dequeue() -> None:
    queue = InMemoryQueue()
    await queue.aclose()
    await queue.aclose()

    with pytest.raises(QueueClosed, match="queue is closed"):
        await queue.enqueue(_envelope())

    with pytest.raises(QueueClosed, match="queue is closed"):
        await queue.dequeue()


async def test_aclose_unblocks_waiting_dequeue_and_stops_background_tasks() -> None:
    queue = InMemoryQueue()

    dequeue_task = asyncio.create_task(queue.dequeue(wait_s=None))
    await asyncio.sleep(0)

    assert queue._delayed_promoter_task is not None
    assert queue._lease_reaper_task is not None
    assert not queue._delayed_promoter_task.done()
    assert not queue._lease_reaper_task.done()

    await queue.aclose()

    assert queue._delayed_promoter_task.done()
    assert queue._lease_reaper_task.done()

    with pytest.raises(QueueClosed, match="queue is closed"):
        await asyncio.wait_for(dequeue_task, timeout=0.5)

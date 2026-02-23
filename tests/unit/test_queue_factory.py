from __future__ import annotations

from uuid import uuid4

from reflexor.config import ReflexorSettings
from reflexor.infra.queue.factory import build_queue
from reflexor.infra.queue.in_memory_queue import InMemoryQueue
from reflexor.orchestrator.queue import TaskEnvelope


async def test_build_queue_defaults_to_inmemory_and_wires_visibility_timeout() -> None:
    now_ms = 0

    def clock() -> int:
        return now_ms

    settings = ReflexorSettings(queue_visibility_timeout_s=7.5)
    queue = build_queue(settings, now_ms=clock)
    assert isinstance(queue, InMemoryQueue)

    envelope = TaskEnvelope(
        envelope_id=str(uuid4()),
        task_id=str(uuid4()),
        run_id=str(uuid4()),
        attempt=0,
        created_at_ms=0,
        available_at_ms=0,
    )
    await queue.enqueue(envelope)

    lease = await queue.dequeue()
    assert lease is not None
    assert lease.visibility_timeout_s == 7.5

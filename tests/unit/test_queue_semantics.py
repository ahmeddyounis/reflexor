from __future__ import annotations

from uuid import uuid4

from reflexor.infra.queue.task_queue_in_memory import InMemoryTaskQueue
from reflexor.orchestrator.queue import TaskEnvelope


def _envelope(*, created_at_ms: int, available_at_ms: int) -> TaskEnvelope:
    return TaskEnvelope(
        envelope_id=str(uuid4()),
        task_id=str(uuid4()),
        run_id=str(uuid4()),
        attempt=0,
        created_at_ms=created_at_ms,
        available_at_ms=available_at_ms,
    )


async def test_ack_removes_message() -> None:
    now_ms = 0

    def clock() -> int:
        return now_ms

    queue = InMemoryTaskQueue(now_ms=clock)
    await queue.enqueue(_envelope(created_at_ms=0, available_at_ms=0))

    lease = await queue.dequeue(timeout_s=5)
    assert lease is not None
    await queue.ack(lease)

    now_ms = 10_000
    assert await queue.dequeue(timeout_s=5) is None


async def test_visibility_timeout_can_redeliver_when_not_acked() -> None:
    now_ms = 0

    def clock() -> int:
        return now_ms

    queue = InMemoryTaskQueue(now_ms=clock)
    envelope = _envelope(created_at_ms=0, available_at_ms=0)
    await queue.enqueue(envelope)

    lease1 = await queue.dequeue(timeout_s=5)
    assert lease1 is not None
    assert lease1.envelope.envelope_id == envelope.envelope_id
    assert lease1.envelope.attempt == 0

    now_ms = 5_001
    lease2 = await queue.dequeue(timeout_s=5)
    assert lease2 is not None
    assert lease2.envelope.envelope_id == envelope.envelope_id
    assert lease2.envelope.attempt == 1


async def test_dequeue_respects_available_at_ms() -> None:
    now_ms = 0

    def clock() -> int:
        return now_ms

    queue = InMemoryTaskQueue(now_ms=clock)
    envelope = _envelope(created_at_ms=0, available_at_ms=10_000)
    await queue.enqueue(envelope)

    assert await queue.dequeue(timeout_s=5) is None

    now_ms = 10_000
    lease = await queue.dequeue(timeout_s=5)
    assert lease is not None
    assert lease.envelope.envelope_id == envelope.envelope_id

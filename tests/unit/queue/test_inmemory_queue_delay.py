from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from reflexor.infra.queue.in_memory_queue import InMemoryQueue
from reflexor.orchestrator.queue import TaskEnvelope


def _manual_clock(start_ms: int = 0) -> tuple[Callable[[], int], Callable[[int], None]]:
    now = start_ms

    def now_ms() -> int:
        return now

    def set_ms(value: int) -> None:
        nonlocal now
        now = value

    return now_ms, set_ms


def _envelope(*, created_at_ms: int, available_at_ms: int) -> TaskEnvelope:
    return TaskEnvelope(
        envelope_id=str(uuid4()),
        task_id=str(uuid4()),
        run_id=str(uuid4()),
        attempt=0,
        created_at_ms=created_at_ms,
        available_at_ms=available_at_ms,
    )


async def test_nack_with_delay_withholds_until_due_and_redelivers() -> None:
    now_ms, set_ms = _manual_clock(0)
    queue = InMemoryQueue(now_ms=now_ms)

    envelope = _envelope(created_at_ms=0, available_at_ms=0)
    await queue.enqueue(envelope)

    lease1 = await queue.dequeue(timeout_s=5)
    assert lease1 is not None
    assert lease1.envelope.envelope_id == envelope.envelope_id
    assert lease1.envelope.attempt == 0

    await queue.nack(lease1, delay_s=10, reason="tests")
    assert await queue.dequeue(timeout_s=5) is None

    set_ms(9_999)
    assert await queue.dequeue(timeout_s=5) is None

    set_ms(10_000)
    lease2 = await queue.dequeue(timeout_s=5)
    assert lease2 is not None
    assert lease2.envelope.envelope_id == envelope.envelope_id
    assert lease2.envelope.attempt == 1


async def test_enqueue_respects_available_at_ms_until_due() -> None:
    now_ms, set_ms = _manual_clock(0)
    queue = InMemoryQueue(now_ms=now_ms)

    envelope = _envelope(created_at_ms=0, available_at_ms=5_000)
    await queue.enqueue(envelope)

    assert await queue.dequeue(timeout_s=5) is None

    set_ms(4_999)
    assert await queue.dequeue(timeout_s=5) is None

    set_ms(5_000)
    lease = await queue.dequeue(timeout_s=5)
    assert lease is not None
    assert lease.envelope.envelope_id == envelope.envelope_id
    assert lease.envelope.attempt == 0


async def test_multiple_delayed_envelopes_release_in_due_time_order_best_effort() -> None:
    now_ms, set_ms = _manual_clock(0)
    queue = InMemoryQueue(now_ms=now_ms)

    env_late = _envelope(created_at_ms=0, available_at_ms=3_000)
    env_early = _envelope(created_at_ms=0, available_at_ms=1_000)
    env_mid = _envelope(created_at_ms=0, available_at_ms=2_000)

    await queue.enqueue(env_late)
    await queue.enqueue(env_early)
    await queue.enqueue(env_mid)

    set_ms(3_000)
    leases = [
        await queue.dequeue(timeout_s=5),
        await queue.dequeue(timeout_s=5),
        await queue.dequeue(timeout_s=5),
    ]
    assert all(lease is not None for lease in leases)
    delivered_ids = [lease.envelope.envelope_id for lease in leases if lease is not None]
    assert delivered_ids == [
        env_early.envelope_id,
        env_mid.envelope_id,
        env_late.envelope_id,
    ]

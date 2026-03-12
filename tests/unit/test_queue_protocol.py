from __future__ import annotations

import math
from uuid import uuid4

import pytest

from reflexor.config import ReflexorSettings
from reflexor.infra.queue.in_memory_queue import InMemoryQueue
from reflexor.orchestrator.queue import Lease, TaskEnvelope


async def test_dequeue_returns_json_serializable_lease_and_envelope() -> None:
    now_ms = 0

    def clock() -> int:
        return now_ms

    queue = InMemoryQueue(now_ms=clock)
    envelope = TaskEnvelope(
        envelope_id=str(uuid4()),
        task_id=str(uuid4()),
        run_id=str(uuid4()),
        attempt=0,
        created_at_ms=0,
        available_at_ms=0,
    )

    await queue.enqueue(envelope)
    lease = await queue.dequeue(timeout_s=5)
    assert lease is not None
    assert lease.attempt == lease.envelope.attempt == 0

    dumped = lease.model_dump(mode="json")
    assert Lease.model_validate(dumped) == lease


async def test_dequeue_uses_default_visibility_timeout_from_settings() -> None:
    now_ms = 0

    def clock() -> int:
        return now_ms

    settings = ReflexorSettings(queue_visibility_timeout_s=7.5)
    queue = InMemoryQueue.from_settings(settings, now_ms=clock)
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


async def test_nack_delays_redelivery_and_increments_attempt() -> None:
    now_ms = 0

    def clock() -> int:
        return now_ms

    queue = InMemoryQueue(now_ms=clock)
    envelope = TaskEnvelope(
        envelope_id=str(uuid4()),
        task_id=str(uuid4()),
        run_id=str(uuid4()),
        attempt=0,
        created_at_ms=0,
        available_at_ms=0,
    )

    await queue.enqueue(envelope)

    lease1 = await queue.dequeue(timeout_s=5)
    assert lease1 is not None
    assert lease1.envelope.attempt == 0

    await queue.nack(lease1, delay_s=10, reason="tests")
    assert await queue.dequeue(timeout_s=5) is None

    now_ms = 10_000
    lease2 = await queue.dequeue(timeout_s=5)
    assert lease2 is not None
    assert lease2.envelope.attempt == 1


def test_in_memory_queue_rejects_non_finite_default_visibility_timeout() -> None:
    with pytest.raises(ValueError, match="default_visibility_timeout_s must be finite and > 0"):
        InMemoryQueue(default_visibility_timeout_s=math.inf)


async def test_dequeue_rejects_non_finite_timeout_and_wait() -> None:
    queue = InMemoryQueue()

    with pytest.raises(ValueError, match="timeout_s must be finite and > 0"):
        await queue.dequeue(timeout_s=math.nan)

    with pytest.raises(ValueError, match="wait_s must be finite and >= 0 when provided"):
        await queue.dequeue(wait_s=math.inf)


async def test_nack_rejects_non_finite_delay() -> None:
    now_ms = 0

    def clock() -> int:
        return now_ms

    queue = InMemoryQueue(now_ms=clock)
    envelope = TaskEnvelope(
        envelope_id=str(uuid4()),
        task_id=str(uuid4()),
        run_id=str(uuid4()),
        attempt=0,
        created_at_ms=0,
        available_at_ms=0,
    )
    await queue.enqueue(envelope)
    lease = await queue.dequeue(timeout_s=5)
    assert lease is not None

    with pytest.raises(ValueError, match="delay_s must be finite and >= 0"):
        await queue.nack(lease, delay_s=math.nan)


def test_lease_rejects_non_finite_visibility_timeout() -> None:
    envelope = TaskEnvelope(
        envelope_id=str(uuid4()),
        task_id=str(uuid4()),
        run_id=str(uuid4()),
        attempt=0,
        created_at_ms=0,
        available_at_ms=0,
    )

    with pytest.raises(ValueError, match="visibility_timeout_s must be finite and > 0"):
        Lease(
            lease_id=str(uuid4()),
            envelope=envelope,
            leased_at_ms=0,
            visibility_timeout_s=math.inf,
            attempt=0,
        )

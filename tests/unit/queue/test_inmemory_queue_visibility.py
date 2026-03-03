from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from reflexor.infra.queue.in_memory_queue import InMemoryQueue
from reflexor.orchestrator.queue import TaskEnvelope
from reflexor.orchestrator.queue.observer import QueueRedeliverObservation


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


class _RecordingObserver:
    def __init__(self) -> None:
        self.redelivers: list[QueueRedeliverObservation] = []

    def on_enqueue(self, observation: object) -> None:
        _ = observation

    def on_dequeue(self, observation: object) -> None:
        _ = observation

    def on_ack(self, observation: object) -> None:
        _ = observation

    def on_nack(self, observation: object) -> None:
        _ = observation

    def on_redeliver(self, observation: QueueRedeliverObservation) -> None:
        self.redelivers.append(observation)


async def test_visibility_timeout_expires_and_redelivers_with_incremented_attempt() -> None:
    now_ms, set_ms = _manual_clock(0)
    queue = InMemoryQueue(now_ms=now_ms)
    envelope = _envelope(created_at_ms=0, available_at_ms=0)
    await queue.enqueue(envelope)

    lease1 = await queue.dequeue(timeout_s=5)
    assert lease1 is not None
    assert lease1.envelope.envelope_id == envelope.envelope_id
    assert lease1.envelope.attempt == 0

    set_ms(5_001)
    lease2 = await queue.dequeue(timeout_s=5)
    assert lease2 is not None
    assert lease2.envelope.envelope_id == envelope.envelope_id
    assert lease2.envelope.attempt == 1

    await queue.ack(lease2)
    assert await queue.dequeue(timeout_s=5) is None


async def test_ack_after_visibility_timeout_is_noop_and_does_not_drop_redelivery() -> None:
    now_ms, set_ms = _manual_clock(0)
    queue = InMemoryQueue(now_ms=now_ms)
    envelope = _envelope(created_at_ms=0, available_at_ms=0)
    await queue.enqueue(envelope)

    lease1 = await queue.dequeue(timeout_s=5)
    assert lease1 is not None

    set_ms(5_001)
    await queue.ack(lease1)

    lease2 = await queue.dequeue(timeout_s=5)
    assert lease2 is not None
    assert lease2.envelope.envelope_id == envelope.envelope_id
    assert lease2.envelope.attempt == 1


async def test_visibility_timeout_redelivery_emits_observer_hook() -> None:
    now_ms, set_ms = _manual_clock(0)
    observer = _RecordingObserver()
    queue = InMemoryQueue(now_ms=now_ms, observer=observer)
    envelope = _envelope(created_at_ms=0, available_at_ms=0)
    await queue.enqueue(envelope)

    lease1 = await queue.dequeue(timeout_s=5)
    assert lease1 is not None

    set_ms(5_001)
    lease2 = await queue.dequeue(timeout_s=5)
    assert lease2 is not None

    assert len(observer.redelivers) == 1
    observation = observer.redelivers[0]
    assert observation.envelope.envelope_id == envelope.envelope_id
    assert observation.expired_lease_id == lease1.lease_id
    assert observation.expired_attempt == 0
    assert observation.deadline_ms == 5_000
    assert observation.now_ms == 5_001

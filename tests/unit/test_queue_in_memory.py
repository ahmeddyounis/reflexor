from __future__ import annotations

from collections.abc import Callable

import pytest

from reflexor.infra.queue.in_memory import InMemoryQueueBackend


def _manual_clock(start_ms: int = 0) -> tuple[Callable[[], int], Callable[[int], None]]:
    current_ms = start_ms

    def now_ms() -> int:
        return current_ms

    def set_ms(value: int) -> None:
        nonlocal current_ms
        current_ms = value

    return now_ms, set_ms


async def test_enqueue_reserve_ack_round_trip() -> None:
    now_ms, _set_ms = _manual_clock()
    queue = InMemoryQueueBackend(now_ms=now_ms)

    message = await queue.enqueue(queue_name="work", payload={"x": 1})
    assert message.attempts == 0

    reserved = await queue.reserve(queue_name="work", lease_ms=10)
    assert reserved is not None
    assert reserved.message_id == message.message_id
    assert reserved.attempts == 1

    await queue.ack(queue_name="work", message_id=reserved.message_id)
    assert await queue.reserve(queue_name="work", lease_ms=10) is None


async def test_nack_requeues_with_delay_and_increments_attempts() -> None:
    now_ms, set_ms = _manual_clock()
    queue = InMemoryQueueBackend(now_ms=now_ms)

    message = await queue.enqueue(queue_name="work", payload={"x": 1})

    reserved1 = await queue.reserve(queue_name="work", lease_ms=10)
    assert reserved1 is not None
    assert reserved1.message_id == message.message_id
    assert reserved1.attempts == 1

    await queue.nack(queue_name="work", message_id=reserved1.message_id, delay_ms=5)
    assert await queue.reserve(queue_name="work", lease_ms=10) is None

    set_ms(6)
    reserved2 = await queue.reserve(queue_name="work", lease_ms=10)
    assert reserved2 is not None
    assert reserved2.message_id == message.message_id
    assert reserved2.attempts == 2


async def test_lease_expiry_makes_message_visible_again() -> None:
    now_ms, set_ms = _manual_clock()
    queue = InMemoryQueueBackend(now_ms=now_ms)

    message = await queue.enqueue(queue_name="work", payload={"x": 1})
    reserved1 = await queue.reserve(queue_name="work", lease_ms=10)
    assert reserved1 is not None
    assert reserved1.attempts == 1

    set_ms(11)
    reserved2 = await queue.reserve(queue_name="work", lease_ms=10)
    assert reserved2 is not None
    assert reserved2.message_id == message.message_id
    assert reserved2.attempts == 2


async def test_available_at_ms_delays_delivery_until_ready() -> None:
    now_ms, set_ms = _manual_clock()
    queue = InMemoryQueueBackend(now_ms=now_ms)

    message = await queue.enqueue(queue_name="work", payload={"x": 1}, available_at_ms=5)

    assert await queue.reserve(queue_name="work", lease_ms=10) is None

    set_ms(5)
    reserved = await queue.reserve(queue_name="work", lease_ms=10)
    assert reserved is not None
    assert reserved.message_id == message.message_id


async def test_dedupe_key_makes_enqueue_idempotent_until_acked() -> None:
    now_ms, _set_ms = _manual_clock()
    queue = InMemoryQueueBackend(now_ms=now_ms)

    message1 = await queue.enqueue(queue_name="work", payload={"x": 1}, dedupe_key="same")
    message2 = await queue.enqueue(queue_name="work", payload={"x": 2}, dedupe_key="same")
    assert message2.message_id == message1.message_id

    reserved = await queue.reserve(queue_name="work", lease_ms=10)
    assert reserved is not None
    await queue.ack(queue_name="work", message_id=reserved.message_id)

    message3 = await queue.enqueue(queue_name="work", payload={"x": 3}, dedupe_key="same")
    assert message3.message_id != message1.message_id


async def test_reserve_rejects_non_positive_lease() -> None:
    queue = InMemoryQueueBackend()
    with pytest.raises(ValueError, match="lease_ms must be > 0"):
        await queue.reserve(queue_name="work", lease_ms=0)

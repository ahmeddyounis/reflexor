from __future__ import annotations

import asyncio
from uuid import uuid4

from reflexor.infra.queue.in_memory_queue import InMemoryQueue
from reflexor.orchestrator.queue import TaskEnvelope


async def test_multi_producer_multi_consumer_at_least_once_no_deadlocks() -> None:
    now_ms = 0

    def clock() -> int:
        return now_ms

    queue = InMemoryQueue(now_ms=clock)

    producers_count = 5
    consumers_count = 10
    total_envelopes = 100

    envelopes: list[TaskEnvelope] = [
        TaskEnvelope(
            envelope_id=str(uuid4()),
            task_id=str(uuid4()),
            run_id=str(uuid4()),
            attempt=0,
            created_at_ms=0,
            available_at_ms=0,
        )
        for _ in range(total_envelopes)
    ]
    expected_ids = {envelope.envelope_id for envelope in envelopes}
    assert len(expected_ids) == total_envelopes

    to_nack_once = {envelope.envelope_id for envelope in envelopes[:15]}

    per_producer = total_envelopes // producers_count
    shards: list[list[TaskEnvelope]] = [
        envelopes[i * per_producer : (i + 1) * per_producer] for i in range(producers_count)
    ]
    remainder = envelopes[producers_count * per_producer :]
    for i, envelope in enumerate(remainder):
        shards[i % producers_count].append(envelope)

    producers_done = asyncio.Event()
    producers_done_count = 0
    state_guard = asyncio.Lock()

    delivery_attempts: dict[str, list[int]] = {}
    acked: set[str] = set()

    async def producer(items: list[TaskEnvelope]) -> None:
        nonlocal producers_done_count
        for envelope in items:
            await queue.enqueue(envelope)

        async with state_guard:
            producers_done_count += 1
            if producers_done_count == producers_count:
                producers_done.set()

    async def consumer() -> None:
        while True:
            async with state_guard:
                if producers_done.is_set() and len(acked) == total_envelopes:
                    return

            lease = await queue.dequeue(timeout_s=30)
            if lease is None:
                await asyncio.sleep(0)
                continue

            envelope_id = lease.envelope.envelope_id
            async with state_guard:
                if envelope_id in acked:
                    raise AssertionError(f"acked envelope was redelivered: {envelope_id!r}")
                attempts = delivery_attempts.setdefault(envelope_id, [])
                attempts.append(lease.envelope.attempt)
                seen_count = len(attempts)

            if envelope_id in to_nack_once and seen_count == 1:
                await queue.nack(lease, delay_s=0, reason="tests")
                continue

            await queue.ack(lease)
            async with state_guard:
                acked.add(envelope_id)

    await asyncio.wait_for(
        asyncio.gather(
            *(producer(shard) for shard in shards),
            *(consumer() for _ in range(consumers_count)),
        ),
        timeout=2.0,
    )

    assert acked == expected_ids
    assert set(delivery_attempts) == expected_ids
    for envelope_id, attempts in delivery_attempts.items():
        if envelope_id in to_nack_once:
            assert attempts == [0, 1]
        else:
            assert attempts == [0]

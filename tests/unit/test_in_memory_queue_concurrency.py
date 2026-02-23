from __future__ import annotations

import asyncio
from uuid import uuid4

from reflexor.infra.queue.in_memory_queue import InMemoryQueue
from reflexor.orchestrator.queue import TaskEnvelope


async def test_multi_consumer_no_loss_with_ack_and_nack() -> None:
    now_ms = 0

    def clock() -> int:
        return now_ms

    queue = InMemoryQueue(now_ms=clock)

    envelopes: list[TaskEnvelope] = [
        TaskEnvelope(
            envelope_id=str(uuid4()),
            task_id=str(uuid4()),
            run_id=str(uuid4()),
            attempt=0,
            created_at_ms=0,
            available_at_ms=0,
        )
        for _ in range(50)
    ]
    for envelope in envelopes:
        await queue.enqueue(envelope)

    to_nack_once = {envelope.envelope_id for envelope in envelopes[:10]}
    delivery_attempts: dict[str, list[int]] = {}
    acked: set[str] = set()
    guard = asyncio.Lock()

    async def consumer() -> None:
        while True:
            async with guard:
                if len(acked) == len(envelopes):
                    return

            lease = await queue.dequeue(timeout_s=5)
            if lease is None:
                await asyncio.sleep(0)
                continue

            envelope_id = lease.envelope.envelope_id
            async with guard:
                delivery_attempts.setdefault(envelope_id, []).append(lease.envelope.attempt)
                seen_count = len(delivery_attempts[envelope_id])

            if envelope_id in to_nack_once and seen_count == 1:
                await queue.nack(lease, delay_s=0, reason="tests")
                continue

            await queue.ack(lease)
            async with guard:
                acked.add(envelope_id)

    await asyncio.wait_for(asyncio.gather(*(consumer() for _ in range(10))), timeout=1.0)

    assert acked == {envelope.envelope_id for envelope in envelopes}

    for envelope in envelopes:
        attempts = delivery_attempts.get(envelope.envelope_id)
        assert attempts is not None
        if envelope.envelope_id in to_nack_once:
            assert attempts == [0, 1]
        else:
            assert attempts == [0]

from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest

redis = pytest.importorskip("redis")

from reflexor.config import ReflexorSettings  # noqa: E402
from reflexor.infra.queue.factory import build_queue  # noqa: E402
from reflexor.infra.queue.redis_streams import RedisStreamsQueue  # noqa: E402
from reflexor.orchestrator.queue import TaskEnvelope  # noqa: E402


def _redis_url() -> str:
    url = os.environ.get("REFLEXOR_TEST_REDIS_URL")
    if not url:
        pytest.skip("REFLEXOR_TEST_REDIS_URL is not set")
    return url.strip()


def _envelope(*, created_at_ms: int, available_at_ms: int) -> TaskEnvelope:
    return TaskEnvelope(
        envelope_id=str(uuid4()),
        task_id=str(uuid4()),
        run_id=str(uuid4()),
        attempt=0,
        created_at_ms=created_at_ms,
        available_at_ms=available_at_ms,
    )


async def _cleanup(url: str, *, keys: list[str]) -> None:
    client = redis.asyncio.Redis.from_url(url, decode_responses=True)
    try:
        if keys:
            await client.delete(*keys)
    finally:
        await client.aclose(close_connection_pool=True)


@pytest.mark.asyncio
async def test_redis_streams_queue_enqueue_dequeue_ack_roundtrip() -> None:
    url = _redis_url()
    stream_key = f"test:reflexor:stream:{uuid4().hex}"
    delayed_key = f"test:reflexor:delayed:{uuid4().hex}"
    group = f"test-group-{uuid4().hex}"
    consumer = f"test-consumer-{uuid4().hex}"

    now_ms = 0

    def clock() -> int:
        return now_ms

    settings = ReflexorSettings(
        queue_backend="redis_streams",
        redis_url=url,
        redis_stream_key=stream_key,
        redis_consumer_group=group,
        redis_consumer_name=consumer,
        redis_delayed_zset_key=delayed_key,
        redis_visibility_timeout_ms=1,
    )
    queue = build_queue(settings, now_ms=clock)
    assert isinstance(queue, RedisStreamsQueue)

    try:
        envelope = _envelope(created_at_ms=0, available_at_ms=0)
        await queue.enqueue(envelope)

        lease = await queue.dequeue(timeout_s=1, wait_s=0.0)
        assert lease is not None
        assert lease.envelope.envelope_id == envelope.envelope_id
        assert lease.envelope.attempt == 0
        assert isinstance(lease.lease_id, str) and lease.lease_id

        await queue.ack(lease)
        assert await queue.dequeue(timeout_s=1, wait_s=0.0) is None
    finally:
        await queue.aclose()
        await _cleanup(url, keys=[stream_key, delayed_key])


@pytest.mark.asyncio
async def test_redis_streams_queue_nack_requeues_with_incremented_attempt() -> None:
    url = _redis_url()
    stream_key = f"test:reflexor:stream:{uuid4().hex}"
    delayed_key = f"test:reflexor:delayed:{uuid4().hex}"
    group = f"test-group-{uuid4().hex}"
    consumer = f"test-consumer-{uuid4().hex}"

    now_ms = 0

    def clock() -> int:
        return now_ms

    queue = RedisStreamsQueue.from_settings(
        ReflexorSettings(
            queue_backend="redis_streams",
            redis_url=url,
            redis_stream_key=stream_key,
            redis_consumer_group=group,
            redis_consumer_name=consumer,
            redis_delayed_zset_key=delayed_key,
            redis_visibility_timeout_ms=1,
        ),
        now_ms=clock,
    )

    try:
        envelope = _envelope(created_at_ms=0, available_at_ms=0)
        await queue.enqueue(envelope)

        lease1 = await queue.dequeue(timeout_s=1, wait_s=0.0)
        assert lease1 is not None
        assert lease1.envelope.attempt == 0

        await queue.nack(lease1, delay_s=0.0, reason="tests")

        lease2 = await queue.dequeue(timeout_s=1, wait_s=0.0)
        assert lease2 is not None
        assert lease2.envelope.envelope_id == envelope.envelope_id
        assert lease2.envelope.attempt == 1

        await queue.ack(lease2)
        assert await queue.dequeue(timeout_s=1, wait_s=0.0) is None
    finally:
        await queue.aclose()
        await _cleanup(url, keys=[stream_key, delayed_key])


@pytest.mark.asyncio
async def test_redis_streams_queue_delay_uses_delayed_zset_then_promotes() -> None:
    url = _redis_url()
    stream_key = f"test:reflexor:stream:{uuid4().hex}"
    delayed_key = f"test:reflexor:delayed:{uuid4().hex}"
    group = f"test-group-{uuid4().hex}"
    consumer = f"test-consumer-{uuid4().hex}"

    now_ms = 0

    def clock() -> int:
        return now_ms

    queue = RedisStreamsQueue.from_settings(
        ReflexorSettings(
            queue_backend="redis_streams",
            redis_url=url,
            redis_stream_key=stream_key,
            redis_consumer_group=group,
            redis_consumer_name=consumer,
            redis_delayed_zset_key=delayed_key,
            redis_visibility_timeout_ms=1,
            redis_promote_batch_size=10,
        ),
        now_ms=clock,
    )

    try:
        envelope = _envelope(created_at_ms=0, available_at_ms=0)
        await queue.enqueue(envelope)

        lease1 = await queue.dequeue(timeout_s=1, wait_s=0.0)
        assert lease1 is not None
        assert lease1.envelope.attempt == 0

        await queue.nack(lease1, delay_s=1.0, reason="tests")

        assert await queue.dequeue(timeout_s=1, wait_s=0.0) is None

        now_ms = 1_000
        lease2 = await queue.dequeue(timeout_s=1, wait_s=0.0)
        assert lease2 is not None
        assert lease2.envelope.envelope_id == envelope.envelope_id
        assert lease2.envelope.attempt == 1

        await queue.ack(lease2)
        assert await queue.dequeue(timeout_s=1, wait_s=0.0) is None
    finally:
        await queue.aclose()
        await _cleanup(url, keys=[stream_key, delayed_key])


@pytest.mark.asyncio
async def test_redis_streams_queue_available_at_ms_future_schedules_to_delayed() -> None:
    url = _redis_url()
    stream_key = f"test:reflexor:stream:{uuid4().hex}"
    delayed_key = f"test:reflexor:delayed:{uuid4().hex}"
    group = f"test-group-{uuid4().hex}"
    consumer = f"test-consumer-{uuid4().hex}"

    now_ms = 0

    def clock() -> int:
        return now_ms

    queue = RedisStreamsQueue.from_settings(
        ReflexorSettings(
            queue_backend="redis_streams",
            redis_url=url,
            redis_stream_key=stream_key,
            redis_consumer_group=group,
            redis_consumer_name=consumer,
            redis_delayed_zset_key=delayed_key,
            redis_visibility_timeout_ms=1,
            redis_promote_batch_size=10,
        ),
        now_ms=clock,
    )

    try:
        envelope = _envelope(created_at_ms=0, available_at_ms=5_000)
        await queue.enqueue(envelope)

        assert await queue.dequeue(timeout_s=1, wait_s=0.0) is None

        now_ms = 5_000
        lease = await queue.dequeue(timeout_s=1, wait_s=0.0)
        assert lease is not None
        assert lease.envelope.envelope_id == envelope.envelope_id
        assert lease.envelope.attempt == 0
        await queue.ack(lease)
    finally:
        await queue.aclose()
        await _cleanup(url, keys=[stream_key, delayed_key])


@pytest.mark.asyncio
async def test_redis_streams_queue_claims_expired_pending_messages() -> None:
    url = _redis_url()
    stream_key = f"test:reflexor:stream:{uuid4().hex}"
    delayed_key = f"test:reflexor:delayed:{uuid4().hex}"
    group = f"test-group-{uuid4().hex}"

    now_ms = 0

    def clock() -> int:
        return now_ms

    queue_a = RedisStreamsQueue.from_settings(
        ReflexorSettings(
            queue_backend="redis_streams",
            redis_url=url,
            redis_stream_key=stream_key,
            redis_consumer_group=group,
            redis_consumer_name="consumer-a",
            redis_delayed_zset_key=delayed_key,
            redis_visibility_timeout_ms=1,
            redis_claim_batch_size=10,
        ),
        now_ms=clock,
    )
    queue_b = RedisStreamsQueue.from_settings(
        ReflexorSettings(
            queue_backend="redis_streams",
            redis_url=url,
            redis_stream_key=stream_key,
            redis_consumer_group=group,
            redis_consumer_name="consumer-b",
            redis_delayed_zset_key=delayed_key,
            redis_visibility_timeout_ms=1,
            redis_claim_batch_size=10,
        ),
        now_ms=clock,
    )

    try:
        envelope = _envelope(created_at_ms=0, available_at_ms=0)
        await queue_a.enqueue(envelope)

        lease_a = await queue_a.dequeue(timeout_s=0.01, wait_s=0.0)
        assert lease_a is not None
        assert lease_a.envelope.attempt == 0

        await asyncio.sleep(0.02)

        lease_b = await queue_b.dequeue(timeout_s=0.01, wait_s=0.0)
        assert lease_b is not None
        assert lease_b.envelope.envelope_id == envelope.envelope_id
        assert lease_b.envelope.attempt == 1

        await queue_b.ack(lease_b)
        assert await queue_b.dequeue(timeout_s=0.01, wait_s=0.0) is None
    finally:
        await queue_a.aclose()
        await queue_b.aclose()
        await _cleanup(url, keys=[stream_key, delayed_key])

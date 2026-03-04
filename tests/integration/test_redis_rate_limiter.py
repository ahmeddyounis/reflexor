from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest

redis = pytest.importorskip("redis")

from reflexor.guards.rate_limit import RateLimitKey, RateLimitSpec  # noqa: E402
from reflexor.infra.guards.redis_rate_limiter import (  # noqa: E402
    RedisRateLimiter,
    RedisRateLimiterConfig,
)


def _redis_url() -> str:
    url = os.environ.get("TEST_REDIS_URL") or os.environ.get("REDIS_URL")
    if not url:
        pytest.skip("TEST_REDIS_URL or REDIS_URL is not set")
    return url.strip()


async def _cleanup(url: str, *, key_prefix: str) -> None:
    client = redis.asyncio.Redis.from_url(url, decode_responses=True)
    try:
        keys: list[str] = []
        async for key in client.scan_iter(match=f"{key_prefix}:*"):
            keys.append(str(key))
        if keys:
            await client.delete(*keys)
    finally:
        await client.aclose(close_connection_pool=True)


@pytest.mark.asyncio
async def test_redis_rate_limiter_is_atomic_under_concurrency() -> None:
    url = _redis_url()
    prefix = f"test:reflexor:rate_limit:{uuid4().hex}"
    key = RateLimitKey(tool_name="tests.atomic")
    spec = RateLimitSpec(capacity=5.0, refill_rate_per_s=0.0, burst=0.0)

    limiter_a = RedisRateLimiter(config=RedisRateLimiterConfig(redis_url=url, key_prefix=prefix))
    limiter_b = RedisRateLimiter(config=RedisRateLimiterConfig(redis_url=url, key_prefix=prefix))
    try:

        async def consume(limiter: RedisRateLimiter) -> bool:
            result = await limiter.consume(key=key, spec=spec, cost=1.0, now_s=0.0)
            return bool(result.allowed)

        results = await asyncio.gather(
            *([consume(limiter_a) for _ in range(10)] + [consume(limiter_b) for _ in range(10)])
        )
        assert sum(1 for allowed in results if allowed) == 5
    finally:
        await limiter_a.aclose()
        await limiter_b.aclose()
        await _cleanup(url, key_prefix=prefix)


@pytest.mark.asyncio
async def test_redis_rate_limiter_retry_after_matches_token_bucket_math() -> None:
    url = _redis_url()
    prefix = f"test:reflexor:rate_limit:{uuid4().hex}"
    key = RateLimitKey(tool_name="tests.retry_after")
    spec = RateLimitSpec(capacity=1.0, refill_rate_per_s=1.0, burst=0.0)

    limiter = RedisRateLimiter(config=RedisRateLimiterConfig(redis_url=url, key_prefix=prefix))
    try:
        first = await limiter.consume(key=key, spec=spec, cost=1.0, now_s=0.0)
        assert first.allowed is True
        assert first.retry_after_s == 0.0

        immediate = await limiter.consume(key=key, spec=spec, cost=1.0, now_s=0.0)
        assert immediate.allowed is False
        assert immediate.retry_after_s == 1.0

        half = await limiter.consume(key=key, spec=spec, cost=1.0, now_s=0.5)
        assert half.allowed is False
        assert half.retry_after_s == 0.5

        full = await limiter.consume(key=key, spec=spec, cost=1.0, now_s=1.0)
        assert full.allowed is True
        assert full.retry_after_s == 0.0
    finally:
        await limiter.aclose()
        await _cleanup(url, key_prefix=prefix)

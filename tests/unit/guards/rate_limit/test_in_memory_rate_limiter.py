from __future__ import annotations

import asyncio

import pytest

from reflexor.guards.rate_limit import InMemoryRateLimiter, RateLimitKey, RateLimitSpec


@pytest.mark.asyncio
async def test_in_memory_rate_limiter_ttl_eviction() -> None:
    limiter = InMemoryRateLimiter(max_keys=100, ttl_s=5.0)
    spec = RateLimitSpec(capacity=1.0, refill_rate_per_s=0.0, burst=0.0)

    k1 = RateLimitKey(tool_name="tests.k1")
    k2 = RateLimitKey(tool_name="tests.k2")

    await limiter.consume(key=k1, spec=spec, cost=0.0, now_s=0.0)
    assert limiter.size == 1

    await limiter.consume(key=k1, spec=spec, cost=0.0, now_s=3.0)
    assert limiter.snapshot_keys() == (k1,)

    await limiter.consume(key=k2, spec=spec, cost=0.0, now_s=10.0)
    assert limiter.size == 1
    assert limiter.snapshot_keys() == (k2,)


@pytest.mark.asyncio
async def test_in_memory_rate_limiter_max_keys_eviction_is_lru() -> None:
    limiter = InMemoryRateLimiter(max_keys=2, ttl_s=3600.0)
    spec = RateLimitSpec(capacity=1.0, refill_rate_per_s=0.0, burst=0.0)

    k1 = RateLimitKey(tool_name="tests.k1")
    k2 = RateLimitKey(tool_name="tests.k2")
    k3 = RateLimitKey(tool_name="tests.k3")

    await limiter.consume(key=k1, spec=spec, cost=0.0, now_s=0.0)
    await limiter.consume(key=k2, spec=spec, cost=0.0, now_s=0.0)
    assert limiter.snapshot_keys() == (k1, k2)

    await limiter.consume(key=k1, spec=spec, cost=0.0, now_s=0.0)
    assert limiter.snapshot_keys() == (k2, k1)

    await limiter.consume(key=k3, spec=spec, cost=0.0, now_s=0.0)
    assert limiter.size == 2
    assert limiter.snapshot_keys() == (k1, k3)


@pytest.mark.asyncio
async def test_in_memory_rate_limiter_memory_is_bounded_by_max_keys() -> None:
    limiter = InMemoryRateLimiter(max_keys=3, ttl_s=3600.0)
    spec = RateLimitSpec(capacity=1.0, refill_rate_per_s=0.0, burst=0.0)

    for idx in range(10):
        key = RateLimitKey(tool_name=f"tests.k{idx}")
        await limiter.consume(key=key, spec=spec, cost=0.0, now_s=0.0)

    assert limiter.size <= 3


@pytest.mark.asyncio
async def test_in_memory_rate_limiter_is_concurrency_safe() -> None:
    limiter = InMemoryRateLimiter(max_keys=10, ttl_s=3600.0)
    spec = RateLimitSpec(capacity=1.0, refill_rate_per_s=0.0, burst=0.0)
    key = RateLimitKey(tool_name="tests.concurrent")

    r1, r2 = await asyncio.gather(
        limiter.consume(key=key, spec=spec, cost=1.0, now_s=0.0),
        limiter.consume(key=key, spec=spec, cost=1.0, now_s=0.0),
    )
    assert (r1.allowed + r2.allowed) == 1


@pytest.mark.asyncio
async def test_in_memory_rate_limiter_rejects_non_finite_ttl_and_time() -> None:
    with pytest.raises(ValueError, match="ttl_s must be finite and > 0"):
        InMemoryRateLimiter(ttl_s=float("nan"))

    limiter = InMemoryRateLimiter(max_keys=10, ttl_s=3600.0)
    spec = RateLimitSpec(capacity=1.0, refill_rate_per_s=0.0, burst=0.0)

    with pytest.raises(ValueError, match="now_s must be finite and >= 0"):
        await limiter.consume(
            key=RateLimitKey(tool_name="tests.invalid"),
            spec=spec,
            cost=0.0,
            now_s=float("inf"),
        )

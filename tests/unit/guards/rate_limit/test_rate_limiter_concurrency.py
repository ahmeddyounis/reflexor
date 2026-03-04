from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from reflexor.guards.rate_limit import (
    AsyncioKeyedLockStrategy,
    NoopKeyedLockStrategy,
    RateLimitKey,
    RateLimitSpec,
    TokenBucketRateLimiter,
    TokenBucketState,
)


@dataclass(slots=True)
class CoordinatedStore:
    """Store that forces interleavings to make races deterministic without locks."""

    expected_reads: int
    max_wait_turns: int = 50
    buckets: dict[RateLimitKey, TokenBucketState] = field(default_factory=dict)

    _reads: int = 0
    _reads_done: asyncio.Event = field(default_factory=asyncio.Event)

    async def load(self, *, key: RateLimitKey) -> TokenBucketState | None:
        state = self.buckets.get(key)
        self._reads += 1
        if self._reads >= int(self.expected_reads):
            self._reads_done.set()
        return state

    async def save(self, *, key: RateLimitKey, state: TokenBucketState) -> None:
        for _ in range(int(self.max_wait_turns)):
            if self._reads_done.is_set():
                break
            await asyncio.sleep(0)
        self.buckets[key] = state


@pytest.mark.asyncio
async def test_rate_limiter_concurrent_consume_is_safe_with_keyed_lock() -> None:
    key = RateLimitKey(tool_name="tests.rate_limit")
    spec = RateLimitSpec(capacity=1.0, refill_rate_per_s=0.0, burst=0.0)
    now_s = 0.0

    store = CoordinatedStore(expected_reads=2, max_wait_turns=50)
    limiter = TokenBucketRateLimiter(store=store, locks=AsyncioKeyedLockStrategy())

    r1, r2 = await asyncio.gather(
        limiter.consume(key=key, spec=spec, cost=1.0, now_s=now_s),
        limiter.consume(key=key, spec=spec, cost=1.0, now_s=now_s),
    )
    assert (r1.allowed + r2.allowed) == 1


@pytest.mark.asyncio
async def test_rate_limiter_races_without_lock_strategy_under_forced_interleavings() -> None:
    key = RateLimitKey(tool_name="tests.rate_limit")
    spec = RateLimitSpec(capacity=1.0, refill_rate_per_s=0.0, burst=0.0)
    now_s = 0.0

    store = CoordinatedStore(expected_reads=2, max_wait_turns=50)
    limiter = TokenBucketRateLimiter(store=store, locks=NoopKeyedLockStrategy())

    r1, r2 = await asyncio.gather(
        limiter.consume(key=key, spec=spec, cost=1.0, now_s=now_s),
        limiter.consume(key=key, spec=spec, cost=1.0, now_s=now_s),
    )
    assert (r1.allowed + r2.allowed) == 2

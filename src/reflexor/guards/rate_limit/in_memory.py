from __future__ import annotations

import asyncio
import math
from collections import OrderedDict

from reflexor.guards.rate_limit.key import RateLimitKey
from reflexor.guards.rate_limit.limiter import RateLimiter, RateLimitResult
from reflexor.guards.rate_limit.spec import RateLimitSpec
from reflexor.guards.rate_limit.token_bucket import TokenBucket, TokenBucketState


class InMemoryRateLimiter(RateLimiter):
    """In-memory RateLimiter with bounded memory (LRU + idle TTL eviction)."""

    def __init__(
        self,
        *,
        max_keys: int = 10_000,
        ttl_s: float = 3600.0,
    ) -> None:
        max_keys_i = int(max_keys)
        ttl_f = float(ttl_s)
        if max_keys_i <= 0:
            raise ValueError("max_keys must be > 0")
        if not math.isfinite(ttl_f) or ttl_f <= 0:
            raise ValueError("ttl_s must be finite and > 0")

        self._max_keys = max_keys_i
        self._ttl_s = ttl_f
        self._lock = asyncio.Lock()
        self._buckets: OrderedDict[RateLimitKey, TokenBucketState] = OrderedDict()

    @property
    def max_keys(self) -> int:
        return int(self._max_keys)

    @property
    def ttl_s(self) -> float:
        return float(self._ttl_s)

    @property
    def size(self) -> int:
        return len(self._buckets)

    def snapshot_keys(self) -> tuple[RateLimitKey, ...]:
        """Return keys in LRU order (oldest first)."""

        return tuple(self._buckets.keys())

    def _is_expired(self, *, state: TokenBucketState, now_s: float) -> bool:
        return (float(now_s) - float(state.updated_at_s)) > float(self._ttl_s)

    def _evict_expired(self, *, now_s: float) -> None:
        while self._buckets:
            oldest_key = next(iter(self._buckets))
            state = self._buckets[oldest_key]
            if not self._is_expired(state=state, now_s=now_s):
                return
            self._buckets.popitem(last=False)

    def _evict_lru(self) -> None:
        while len(self._buckets) > int(self._max_keys):
            self._buckets.popitem(last=False)

    async def consume(
        self,
        *,
        key: RateLimitKey,
        spec: RateLimitSpec,
        cost: float,
        now_s: float,
    ) -> RateLimitResult:
        now = float(now_s)
        if not math.isfinite(now) or now < 0:
            raise ValueError("now_s must be finite and >= 0")

        async with self._lock:
            self._evict_expired(now_s=now)

            state = self._buckets.get(key)
            bucket = TokenBucket.from_state(spec, state, now_s=now)
            allowed, retry_after_s = bucket.consume(cost=float(cost), now_s=now)

            self._buckets[key] = bucket.state
            self._buckets.move_to_end(key, last=True)

            self._evict_lru()

            return RateLimitResult(allowed=bool(allowed), retry_after_s=retry_after_s)


__all__ = ["InMemoryRateLimiter"]

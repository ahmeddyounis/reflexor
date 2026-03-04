from __future__ import annotations

from reflexor.guards.rate_limit.key import RateLimitKey
from reflexor.guards.rate_limit.limiter import RateLimiter, RateLimitResult
from reflexor.guards.rate_limit.locks import KeyedLockStrategy, NoopKeyedLockStrategy
from reflexor.guards.rate_limit.spec import RateLimitSpec
from reflexor.guards.rate_limit.store import TokenBucketStore
from reflexor.guards.rate_limit.token_bucket import TokenBucket


class TokenBucketRateLimiter(RateLimiter):
    """RateLimiter implementation backed by token-bucket math + injected store/locks."""

    def __init__(
        self,
        *,
        store: TokenBucketStore,
        locks: KeyedLockStrategy | None = None,
    ) -> None:
        self._store = store
        self._locks = NoopKeyedLockStrategy() if locks is None else locks

    async def consume(
        self,
        *,
        key: RateLimitKey,
        spec: RateLimitSpec,
        cost: float,
        now_s: float,
    ) -> RateLimitResult:
        async with self._locks.lock(key):
            state = await self._store.load(key=key)
            bucket = TokenBucket.from_state(spec, state, now_s=float(now_s))
            allowed, retry_after_s = bucket.consume(cost=float(cost), now_s=float(now_s))
            await self._store.save(key=key, state=bucket.state)
            return RateLimitResult(allowed=bool(allowed), retry_after_s=retry_after_s)


__all__ = ["TokenBucketRateLimiter"]

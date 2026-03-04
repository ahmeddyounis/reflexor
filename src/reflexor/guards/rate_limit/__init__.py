"""Rate limiting primitives for execution guards.

This package is intentionally pure and storage-agnostic:
- token bucket math lives in `TokenBucket`
- persistence/locking concerns are modeled as narrow ports (Protocols)
"""

from reflexor.guards.rate_limit.key import RateLimitKey
from reflexor.guards.rate_limit.limiter import RateLimiter, RateLimitResult
from reflexor.guards.rate_limit.locks import (
    AsyncioKeyedLockStrategy,
    KeyedLockStrategy,
    NoopKeyedLockStrategy,
)
from reflexor.guards.rate_limit.spec import RateLimitSpec
from reflexor.guards.rate_limit.store import InMemoryTokenBucketStore, TokenBucketStore
from reflexor.guards.rate_limit.token_bucket import TokenBucket, TokenBucketState
from reflexor.guards.rate_limit.token_bucket_limiter import TokenBucketRateLimiter

__all__ = [
    "AsyncioKeyedLockStrategy",
    "InMemoryTokenBucketStore",
    "KeyedLockStrategy",
    "NoopKeyedLockStrategy",
    "RateLimitKey",
    "RateLimitResult",
    "RateLimitSpec",
    "RateLimiter",
    "TokenBucket",
    "TokenBucketRateLimiter",
    "TokenBucketState",
    "TokenBucketStore",
]

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from reflexor.guards.rate_limit.key import RateLimitKey
from reflexor.guards.rate_limit.token_bucket import TokenBucketState


class TokenBucketStore(Protocol):
    async def load(self, *, key: RateLimitKey) -> TokenBucketState | None: ...

    async def save(self, *, key: RateLimitKey, state: TokenBucketState) -> None: ...


@dataclass(slots=True)
class InMemoryTokenBucketStore:
    """In-memory store for token buckets (intended for tests/dev)."""

    buckets: dict[RateLimitKey, TokenBucketState] = field(default_factory=dict)

    async def load(self, *, key: RateLimitKey) -> TokenBucketState | None:
        return self.buckets.get(key)

    async def save(self, *, key: RateLimitKey, state: TokenBucketState) -> None:
        self.buckets[key] = state


__all__ = ["InMemoryTokenBucketStore", "TokenBucketStore"]

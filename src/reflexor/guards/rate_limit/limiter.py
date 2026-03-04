from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from reflexor.guards.rate_limit.key import RateLimitKey
from reflexor.guards.rate_limit.spec import RateLimitSpec


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    allowed: bool
    retry_after_s: float | None


class RateLimiter(Protocol):
    async def consume(
        self,
        *,
        key: RateLimitKey,
        spec: RateLimitSpec,
        cost: float,
        now_s: float,
    ) -> RateLimitResult: ...


__all__ = ["RateLimitResult", "RateLimiter"]

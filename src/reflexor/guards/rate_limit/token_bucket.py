from __future__ import annotations

import math
from dataclasses import dataclass

from reflexor.guards.rate_limit.spec import RateLimitSpec


@dataclass(frozen=True, slots=True)
class TokenBucketState:
    tokens: float
    updated_at_s: float

    def __post_init__(self) -> None:
        tokens = float(self.tokens)
        updated_at = float(self.updated_at_s)
        if not math.isfinite(tokens) or tokens < 0:
            raise ValueError("tokens must be finite and >= 0")
        if not math.isfinite(updated_at) or updated_at < 0:
            raise ValueError("updated_at_s must be finite and >= 0")
        object.__setattr__(self, "tokens", tokens)
        object.__setattr__(self, "updated_at_s", updated_at)


@dataclass(slots=True)
class TokenBucket:
    """Pure token bucket (no IO, no time source).

    This object is intentionally small and easy to snapshot into storage.
    """

    spec: RateLimitSpec
    tokens: float
    updated_at_s: float

    def __post_init__(self) -> None:
        tokens = float(self.tokens)
        updated_at = float(self.updated_at_s)
        if not math.isfinite(tokens) or tokens < 0:
            raise ValueError("tokens must be finite and >= 0")
        if not math.isfinite(updated_at) or updated_at < 0:
            raise ValueError("updated_at_s must be finite and >= 0")
        self.tokens = tokens
        self.updated_at_s = updated_at

    @classmethod
    def new(cls, spec: RateLimitSpec, *, now_s: float) -> TokenBucket:
        now = float(now_s)
        if not math.isfinite(now) or now < 0:
            raise ValueError("now_s must be finite and >= 0")
        return cls(spec=spec, tokens=spec.max_tokens, updated_at_s=now)

    @classmethod
    def from_state(
        cls, spec: RateLimitSpec, state: TokenBucketState | None, *, now_s: float
    ) -> TokenBucket:
        if state is None:
            return cls.new(spec, now_s=now_s)
        return cls(spec=spec, tokens=float(state.tokens), updated_at_s=float(state.updated_at_s))

    @property
    def state(self) -> TokenBucketState:
        return TokenBucketState(tokens=float(self.tokens), updated_at_s=float(self.updated_at_s))

    def _refill(self, *, now_s: float) -> None:
        now = float(now_s)
        if not math.isfinite(now) or now < 0:
            raise ValueError("now_s must be finite and >= 0")

        elapsed_s = max(0.0, now - float(self.updated_at_s))
        if elapsed_s <= 0:
            self.updated_at_s = now
            return

        refill = elapsed_s * float(self.spec.refill_rate_per_s)
        max_tokens = float(self.spec.max_tokens)
        self.tokens = min(max_tokens, float(self.tokens) + refill)
        self.updated_at_s = now

    def consume(self, *, cost: float, now_s: float) -> tuple[bool, float | None]:
        """Consume tokens if available.

        Returns:
        - (True, 0.0) when allowed (tokens consumed)
        - (False, retry_after_s) when not allowed (no consumption)
        - retry_after_s is None when the request can never be satisfied (e.g., cost > max_tokens
          or refill_rate_per_s == 0)
        """

        cost_f = float(cost)
        if not math.isfinite(cost_f) or cost_f < 0:
            raise ValueError("cost must be finite and >= 0")

        self._refill(now_s=now_s)

        max_tokens = float(self.spec.max_tokens)
        if cost_f > max_tokens:
            return False, None

        if cost_f <= float(self.tokens):
            self.tokens = float(self.tokens) - cost_f
            return True, 0.0

        deficit = cost_f - float(self.tokens)
        refill_rate = float(self.spec.refill_rate_per_s)
        if refill_rate <= 0:
            return False, None
        return False, float(deficit) / refill_rate


__all__ = ["TokenBucket", "TokenBucketState"]

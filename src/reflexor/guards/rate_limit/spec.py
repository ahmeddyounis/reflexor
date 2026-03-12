from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RateLimitSpec:
    """Token-bucket configuration (pure, stable).

    Semantics:
    - `capacity`: steady-state max tokens.
    - `burst`: additional tokens allowed above `capacity`.
    - The bucket clamps to `max_tokens = capacity + burst`.
    - Tokens refill at `refill_rate_per_s` tokens/second.
    """

    capacity: float
    refill_rate_per_s: float
    burst: float = 0.0

    def __post_init__(self) -> None:
        capacity = float(self.capacity)
        refill = float(self.refill_rate_per_s)
        burst = float(self.burst)

        if not math.isfinite(capacity) or capacity < 0:
            raise ValueError("capacity must be finite and >= 0")
        if not math.isfinite(refill) or refill < 0:
            raise ValueError("refill_rate_per_s must be finite and >= 0")
        if not math.isfinite(burst) or burst < 0:
            raise ValueError("burst must be finite and >= 0")
        if capacity + burst <= 0:
            raise ValueError("capacity + burst must be > 0")

        object.__setattr__(self, "capacity", capacity)
        object.__setattr__(self, "refill_rate_per_s", refill)
        object.__setattr__(self, "burst", burst)

    @property
    def max_tokens(self) -> float:
        return float(self.capacity) + float(self.burst)


__all__ = ["RateLimitSpec"]

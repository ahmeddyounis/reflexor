from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CircuitBreakerSpec:
    """Standard circuit-breaker configuration (pure, stable).

    Parameters:
    - `failure_threshold`: number of failures within `window_s` to open the circuit.
    - `window_s`: sliding window length for counting failures (seconds).
    - `open_cooldown_s`: time to remain OPEN before allowing HALF_OPEN probes (seconds).
    - `half_open_max_calls`: max concurrent probe calls allowed in HALF_OPEN.
    - `success_threshold`: number of successful HALF_OPEN calls required to close.
    """

    failure_threshold: int
    window_s: float
    open_cooldown_s: float
    half_open_max_calls: int
    success_threshold: int

    def __post_init__(self) -> None:
        try:
            failure_threshold = int(self.failure_threshold)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("failure_threshold must be > 0") from exc
        window_s = float(self.window_s)
        open_cooldown_s = float(self.open_cooldown_s)
        try:
            half_open_max_calls = int(self.half_open_max_calls)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("half_open_max_calls must be > 0") from exc
        try:
            success_threshold = int(self.success_threshold)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("success_threshold must be > 0") from exc

        if failure_threshold <= 0:
            raise ValueError("failure_threshold must be > 0")
        if not math.isfinite(window_s) or window_s <= 0:
            raise ValueError("window_s must be finite and > 0")
        if not math.isfinite(open_cooldown_s) or open_cooldown_s < 0:
            raise ValueError("open_cooldown_s must be finite and >= 0")
        if half_open_max_calls <= 0:
            raise ValueError("half_open_max_calls must be > 0")
        if success_threshold <= 0:
            raise ValueError("success_threshold must be > 0")

        object.__setattr__(self, "failure_threshold", failure_threshold)
        object.__setattr__(self, "window_s", window_s)
        object.__setattr__(self, "open_cooldown_s", open_cooldown_s)
        object.__setattr__(self, "half_open_max_calls", half_open_max_calls)
        object.__setattr__(self, "success_threshold", success_threshold)

    @property
    def half_open_permit_limit(self) -> int:
        return min(int(self.half_open_max_calls), int(self.success_threshold))


__all__ = ["CircuitBreakerSpec"]

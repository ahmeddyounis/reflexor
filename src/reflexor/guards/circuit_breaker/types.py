from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True, slots=True)
class CircuitBreakerDecision:
    allowed: bool
    state: CircuitState
    retry_after_s: float | None = None


__all__ = ["CircuitBreakerDecision", "CircuitState"]

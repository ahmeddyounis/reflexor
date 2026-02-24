"""Concurrency primitives for the executor.

This module is intentionally small and dependency-light. The executor uses it to cap parallelism and
avoid resource exhaustion. Concrete concurrency strategies can evolve without changing the worker
runtime or queue interface.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ConcurrencyLimits:
    """Basic executor concurrency limits.

    Values must be positive integers.
    """

    max_in_flight: int = 10

    def __post_init__(self) -> None:
        if int(self.max_in_flight) <= 0:
            raise ValueError("max_in_flight must be > 0")

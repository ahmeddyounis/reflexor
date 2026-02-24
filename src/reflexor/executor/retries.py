"""Retry helpers for executor task execution.

Policies here should be deterministic and easy to test. Backoff strategies are kept simple to avoid
surprising behavior under load.
"""

from __future__ import annotations


def exponential_backoff_s(
    attempt: int,
    *,
    base_delay_s: float = 1.0,
    max_delay_s: float = 60.0,
) -> float:
    """Compute a capped exponential backoff delay in seconds.

    `attempt` is 1-based (attempt=1 returns base_delay_s).
    """

    attempt_i = int(attempt)
    if attempt_i <= 0:
        raise ValueError("attempt must be >= 1")
    delay = base_delay_s * (2 ** (attempt_i - 1))
    return min(float(max_delay_s), float(delay))

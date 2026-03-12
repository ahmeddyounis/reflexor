from __future__ import annotations

from pathlib import Path

import pytest

from reflexor.config import ReflexorSettings
from reflexor.guards.rate_limit.guard import RateLimitGuard
from reflexor.guards.rate_limit.in_memory import InMemoryRateLimiter
from reflexor.guards.rate_limit.policy import RateLimitPolicy


def test_rate_limit_guard_rejects_invalid_cost(tmp_path: Path) -> None:
    settings = ReflexorSettings(workspace_root=tmp_path)
    policy = RateLimitPolicy(settings=settings, limiter=InMemoryRateLimiter())

    with pytest.raises(ValueError, match="cost must be finite and >= 0"):
        RateLimitGuard(policy=policy, cost=float("nan"))

    with pytest.raises(ValueError, match="cost must be finite and >= 0"):
        RateLimitGuard(policy=policy, cost=-1.0)

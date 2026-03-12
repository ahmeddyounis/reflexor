from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from reflexor.guards.circuit_breaker import CircuitBreakerKey, CircuitBreakerSpec
from reflexor.guards.rate_limit import RateLimitKey, RateLimitSpec
from reflexor.infra.guards.redis_circuit_breaker import (
    RedisCircuitBreaker,
    RedisCircuitBreakerConfig,
)
from reflexor.infra.guards.redis_rate_limiter import RedisRateLimiter, RedisRateLimiterConfig


@dataclass(slots=True)
class _FakeRedisEvalClient:
    responses: list[Any] = field(default_factory=list)
    eval_calls: list[tuple[str, int, tuple[str, ...]]] = field(default_factory=list)
    closed: bool = False

    async def eval(self, script: str, numkeys: int, *keys_and_args: str) -> Any:
        self.eval_calls.append((script, numkeys, keys_and_args))
        if self.responses:
            return self.responses.pop(0)
        return [1, 0]

    async def aclose(self, *, close_connection_pool: bool = True) -> None:
        _ = close_connection_pool
        self.closed = True


@pytest.mark.asyncio
async def test_redis_rate_limiter_rejects_invalid_runtime_inputs_and_rounds_ttl() -> None:
    with pytest.raises(ValueError, match="ttl_s must be finite and > 0 when set"):
        RedisRateLimiterConfig(redis_url="redis://localhost:6379/0", ttl_s=float("nan"))

    client = _FakeRedisEvalClient()
    limiter = RedisRateLimiter(
        config=RedisRateLimiterConfig(redis_url="redis://localhost:6379/0", ttl_s=0.0001),
        client=client,
    )
    key = RateLimitKey(tool_name="tests.tool")
    spec = RateLimitSpec(capacity=1.0, refill_rate_per_s=1.0, burst=0.0)

    with pytest.raises(ValueError, match="now_s must be finite and >= 0"):
        await limiter.consume(key=key, spec=spec, cost=1.0, now_s=float("inf"))

    with pytest.raises(ValueError, match="cost must be finite and >= 0"):
        await limiter.consume(key=key, spec=spec, cost=-1.0, now_s=0.0)

    await limiter.consume(key=key, spec=spec, cost=1.0, now_s=0.0)
    assert client.eval_calls[-1][2][-1] == "1"


@pytest.mark.asyncio
async def test_redis_circuit_breaker_rejects_invalid_runtime_inputs_and_rounds_durations() -> None:
    with pytest.raises(ValueError, match="ttl_s must be finite and > 0 when set"):
        RedisCircuitBreakerConfig(redis_url="redis://localhost:6379/0", ttl_s=float("inf"))

    client = _FakeRedisEvalClient(responses=[[1, "closed", -1], 1])
    breaker = RedisCircuitBreaker(
        spec=CircuitBreakerSpec(
            failure_threshold=1,
            window_s=0.0001,
            open_cooldown_s=0.0001,
            half_open_max_calls=1,
            success_threshold=1,
        ),
        config=RedisCircuitBreakerConfig(redis_url="redis://localhost:6379/0", ttl_s=0.0001),
        client=client,
    )
    key = CircuitBreakerKey(tool_name="tests.tool")

    with pytest.raises(ValueError, match="now_s must be finite and >= 0"):
        await breaker.allow_call(key=key, now_s=float("nan"))

    await breaker.allow_call(key=key, now_s=0.0)
    _, _, args = client.eval_calls[-1]
    assert args[4] == "1"
    assert args[5] == "1"
    assert args[8] == "1"

    with pytest.raises(ValueError, match="now_s must be finite and >= 0"):
        await breaker.record_result(key=key, ok=False, now_s=float("inf"))

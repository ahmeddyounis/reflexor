from __future__ import annotations

import os
from uuid import uuid4

import pytest

redis = pytest.importorskip("redis")

from reflexor.guards.circuit_breaker import (  # noqa: E402
    CircuitBreakerKey,
    CircuitBreakerSpec,
    CircuitState,
)
from reflexor.infra.guards.redis_circuit_breaker import (  # noqa: E402
    RedisCircuitBreaker,
    RedisCircuitBreakerConfig,
)


def _redis_url() -> str:
    url = os.environ.get("TEST_REDIS_URL") or os.environ.get("REDIS_URL")
    if not url:
        pytest.skip("TEST_REDIS_URL or REDIS_URL is not set")
    return url.strip()


async def _cleanup(url: str, *, key_prefix: str) -> None:
    client = redis.asyncio.Redis.from_url(url, decode_responses=True)
    try:
        keys: list[str] = []
        async for key in client.scan_iter(match=f"{key_prefix}:*"):
            keys.append(str(key))
        if keys:
            await client.delete(*keys)
    finally:
        await client.aclose(close_connection_pool=True)


@pytest.mark.asyncio
async def test_redis_circuit_breaker_open_is_shared_across_instances() -> None:
    url = _redis_url()
    prefix = f"test:reflexor:circuit_breaker:{uuid4().hex}"
    spec = CircuitBreakerSpec(
        failure_threshold=2,
        window_s=10.0,
        open_cooldown_s=5.0,
        half_open_max_calls=1,
        success_threshold=1,
    )
    key = CircuitBreakerKey(tool_name="tests.circuit_breaker")

    breaker_a = RedisCircuitBreaker(
        spec=spec,
        config=RedisCircuitBreakerConfig(redis_url=url, key_prefix=prefix),
    )
    breaker_b = RedisCircuitBreaker(
        spec=spec,
        config=RedisCircuitBreakerConfig(redis_url=url, key_prefix=prefix),
    )
    try:
        await breaker_a.record_result(key=key, ok=False, now_s=0.0)
        await breaker_a.record_result(key=key, ok=False, now_s=0.0)

        decision_b = await breaker_b.allow_call(key=key, now_s=0.0)
        assert decision_b.allowed is False
        assert decision_b.state == CircuitState.OPEN
        assert decision_b.retry_after_s == pytest.approx(5.0)
    finally:
        await breaker_a.aclose()
        await breaker_b.aclose()
        await _cleanup(url, key_prefix=prefix)

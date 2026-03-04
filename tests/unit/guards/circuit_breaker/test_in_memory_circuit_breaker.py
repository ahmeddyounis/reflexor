from __future__ import annotations

from dataclasses import dataclass

import pytest

from reflexor.guards.circuit_breaker import (
    CircuitBreakerKey,
    CircuitBreakerSpec,
    CircuitState,
    InMemoryCircuitBreaker,
)


@pytest.mark.asyncio
async def test_circuit_breaker_defaults_to_closed_and_allows_calls() -> None:
    spec = CircuitBreakerSpec(
        failure_threshold=2,
        window_s=10.0,
        open_cooldown_s=5.0,
        half_open_max_calls=1,
        success_threshold=1,
    )
    breaker = InMemoryCircuitBreaker(spec=spec, max_keys=100, ttl_s=3600.0)
    key = CircuitBreakerKey(tool_name="tests.tool")

    decision = await breaker.allow_call(key=key, now_s=0.0)
    assert decision.allowed is True
    assert decision.state == CircuitState.CLOSED
    assert decision.retry_after_s is None


@pytest.mark.asyncio
async def test_failures_within_window_trip_open_and_fail_fast_with_retry_after() -> None:
    @dataclass(slots=True)
    class FakeClock:
        now_s: float

        def now(self) -> float:
            return float(self.now_s)

    clock = FakeClock(now_s=0.0)
    spec = CircuitBreakerSpec(
        failure_threshold=2,
        window_s=10.0,
        open_cooldown_s=5.0,
        half_open_max_calls=1,
        success_threshold=1,
    )
    breaker = InMemoryCircuitBreaker(spec=spec)
    key = CircuitBreakerKey(tool_name="tests.tool")

    await breaker.record_result(key=key, ok=False, now_s=clock.now())

    clock.now_s = 1.0
    await breaker.record_result(key=key, ok=False, now_s=clock.now())

    decision = await breaker.allow_call(key=key, now_s=clock.now())
    assert decision.allowed is False
    assert decision.state == CircuitState.OPEN
    assert decision.retry_after_s == pytest.approx(5.0)

    clock.now_s = 3.0
    decision = await breaker.allow_call(key=key, now_s=clock.now())
    assert decision.allowed is False
    assert decision.state == CircuitState.OPEN
    assert decision.retry_after_s == pytest.approx(3.0)


@pytest.mark.asyncio
async def test_failures_outside_window_do_not_trip_open() -> None:
    spec = CircuitBreakerSpec(
        failure_threshold=2,
        window_s=1.0,
        open_cooldown_s=5.0,
        half_open_max_calls=1,
        success_threshold=1,
    )
    breaker = InMemoryCircuitBreaker(spec=spec)
    key = CircuitBreakerKey(tool_name="tests.tool")

    await breaker.record_result(key=key, ok=False, now_s=0.0)
    await breaker.record_result(key=key, ok=False, now_s=2.0)

    decision = await breaker.allow_call(key=key, now_s=2.0)
    assert decision.allowed is True
    assert decision.state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_open_cooldown_transitions_to_half_open_and_success_closes() -> None:
    spec = CircuitBreakerSpec(
        failure_threshold=1,
        window_s=10.0,
        open_cooldown_s=5.0,
        half_open_max_calls=1,
        success_threshold=1,
    )
    breaker = InMemoryCircuitBreaker(spec=spec)
    key = CircuitBreakerKey(tool_name="tests.tool")

    await breaker.record_result(key=key, ok=False, now_s=0.0)

    denied = await breaker.allow_call(key=key, now_s=4.0)
    assert denied.allowed is False
    assert denied.state == CircuitState.OPEN
    assert denied.retry_after_s == pytest.approx(1.0)

    probe = await breaker.allow_call(key=key, now_s=5.0)
    assert probe.allowed is True
    assert probe.state == CircuitState.HALF_OPEN

    await breaker.record_result(key=key, ok=True, now_s=5.0)

    closed = await breaker.allow_call(key=key, now_s=5.0)
    assert closed.allowed is True
    assert closed.state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_half_open_max_calls_limits_concurrent_probes() -> None:
    spec = CircuitBreakerSpec(
        failure_threshold=1,
        window_s=10.0,
        open_cooldown_s=0.0,
        half_open_max_calls=2,
        success_threshold=10,
    )
    breaker = InMemoryCircuitBreaker(spec=spec)
    key = CircuitBreakerKey(tool_name="tests.tool")

    await breaker.record_result(key=key, ok=False, now_s=0.0)

    probe1 = await breaker.allow_call(key=key, now_s=0.0)
    assert probe1.allowed is True
    assert probe1.state == CircuitState.HALF_OPEN

    probe2 = await breaker.allow_call(key=key, now_s=0.0)
    assert probe2.allowed is True
    assert probe2.state == CircuitState.HALF_OPEN

    denied = await breaker.allow_call(key=key, now_s=0.0)
    assert denied.allowed is False
    assert denied.state == CircuitState.HALF_OPEN

    await breaker.record_result(key=key, ok=True, now_s=0.0)

    probe3 = await breaker.allow_call(key=key, now_s=0.0)
    assert probe3.allowed is True
    assert probe3.state == CircuitState.HALF_OPEN


@pytest.mark.asyncio
async def test_half_open_success_threshold_requires_multiple_successes_to_close() -> None:
    spec = CircuitBreakerSpec(
        failure_threshold=1,
        window_s=10.0,
        open_cooldown_s=0.0,
        half_open_max_calls=1,
        success_threshold=2,
    )
    breaker = InMemoryCircuitBreaker(spec=spec)
    key = CircuitBreakerKey(tool_name="tests.tool")

    await breaker.record_result(key=key, ok=False, now_s=0.0)

    probe1 = await breaker.allow_call(key=key, now_s=0.0)
    assert probe1.allowed is True
    assert probe1.state == CircuitState.HALF_OPEN
    await breaker.record_result(key=key, ok=True, now_s=0.0)

    still_half_open = await breaker.allow_call(key=key, now_s=0.0)
    assert still_half_open.allowed is True
    assert still_half_open.state == CircuitState.HALF_OPEN
    await breaker.record_result(key=key, ok=True, now_s=0.0)

    closed = await breaker.allow_call(key=key, now_s=0.0)
    assert closed.allowed is True
    assert closed.state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_half_open_failure_reopens_and_resets_cooldown() -> None:
    spec = CircuitBreakerSpec(
        failure_threshold=1,
        window_s=10.0,
        open_cooldown_s=5.0,
        half_open_max_calls=1,
        success_threshold=2,
    )
    breaker = InMemoryCircuitBreaker(spec=spec)
    key = CircuitBreakerKey(tool_name="tests.tool")

    await breaker.record_result(key=key, ok=False, now_s=0.0)

    probe = await breaker.allow_call(key=key, now_s=5.0)
    assert probe.allowed is True
    assert probe.state == CircuitState.HALF_OPEN

    await breaker.record_result(key=key, ok=False, now_s=5.0)

    denied = await breaker.allow_call(key=key, now_s=5.0)
    assert denied.allowed is False
    assert denied.state == CircuitState.OPEN
    assert denied.retry_after_s == pytest.approx(5.0)


@pytest.mark.asyncio
async def test_in_memory_circuit_breaker_ttl_eviction_removes_idle_keys() -> None:
    spec = CircuitBreakerSpec(
        failure_threshold=1,
        window_s=10.0,
        open_cooldown_s=1.0,
        half_open_max_calls=1,
        success_threshold=1,
    )
    breaker = InMemoryCircuitBreaker(spec=spec, max_keys=100, ttl_s=1.0)

    k1 = CircuitBreakerKey(tool_name="tests.k1")
    k2 = CircuitBreakerKey(tool_name="tests.k2")

    await breaker.allow_call(key=k1, now_s=0.0)
    await breaker.allow_call(key=k2, now_s=0.5)
    assert breaker.size == 2

    await breaker.allow_call(key=k2, now_s=1.4)
    assert breaker.size == 1
    assert breaker.snapshot_keys() == (k2,)


@pytest.mark.asyncio
async def test_in_memory_circuit_breaker_max_keys_eviction_is_lru() -> None:
    spec = CircuitBreakerSpec(
        failure_threshold=1,
        window_s=10.0,
        open_cooldown_s=1.0,
        half_open_max_calls=1,
        success_threshold=1,
    )
    breaker = InMemoryCircuitBreaker(spec=spec, max_keys=2, ttl_s=3600.0)

    k1 = CircuitBreakerKey(tool_name="tests.k1")
    k2 = CircuitBreakerKey(tool_name="tests.k2")
    k3 = CircuitBreakerKey(tool_name="tests.k3")

    await breaker.allow_call(key=k1, now_s=0.0)
    await breaker.allow_call(key=k2, now_s=0.0)
    await breaker.allow_call(key=k1, now_s=0.0)  # Touch k1 so k2 becomes LRU.
    await breaker.allow_call(key=k3, now_s=0.0)

    assert breaker.size == 2
    assert breaker.snapshot_keys() == (k1, k3)

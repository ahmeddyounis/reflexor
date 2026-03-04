"""Default wiring helpers for guard chains (composition)."""

from __future__ import annotations

from reflexor.guards import GuardChain, PolicyGuard
from reflexor.guards.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerGuard,
    CircuitBreakerSpec,
    InMemoryCircuitBreaker,
)
from reflexor.guards.interface import ExecutionGuard
from reflexor.guards.rate_limit import InMemoryRateLimiter
from reflexor.guards.rate_limit.guard import RateLimitGuard
from reflexor.guards.rate_limit.policy import RateLimitPolicy
from reflexor.observability.metrics import ReflexorMetrics
from reflexor.security.policy.gate import PolicyGate

_DEFAULT_CIRCUIT_BREAKER_SPEC = CircuitBreakerSpec(
    failure_threshold=5,
    window_s=60.0,
    open_cooldown_s=10.0,
    half_open_max_calls=1,
    success_threshold=1,
)


def build_default_circuit_breaker() -> CircuitBreaker:
    return InMemoryCircuitBreaker(spec=_DEFAULT_CIRCUIT_BREAKER_SPEC)


def build_default_policy_guard_chain(
    *,
    gate: PolicyGate,
    metrics: ReflexorMetrics | None = None,
    circuit_breaker: CircuitBreaker | None = None,
) -> GuardChain:
    rate_limiter = InMemoryRateLimiter()
    rate_limit_policy = RateLimitPolicy(settings=gate.settings, limiter=rate_limiter)
    guards: list[ExecutionGuard] = [PolicyGuard(gate=gate)]
    if circuit_breaker is not None:
        guards.append(CircuitBreakerGuard(breaker=circuit_breaker, metrics=metrics))
    guards.append(RateLimitGuard(policy=rate_limit_policy))
    return GuardChain(guards)


__all__ = [
    "build_default_circuit_breaker",
    "build_default_policy_guard_chain",
]

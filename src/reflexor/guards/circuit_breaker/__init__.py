"""Circuit breaker primitives for execution guards.

This package provides a storage-agnostic Protocol and an in-memory implementation.
It is intentionally free of infrastructure/framework imports.
"""

from reflexor.guards.circuit_breaker.in_memory import InMemoryCircuitBreaker
from reflexor.guards.circuit_breaker.interface import CircuitBreaker
from reflexor.guards.circuit_breaker.key import CircuitBreakerKey
from reflexor.guards.circuit_breaker.spec import CircuitBreakerSpec
from reflexor.guards.circuit_breaker.types import CircuitBreakerDecision, CircuitState

__all__ = [
    "CircuitBreaker",
    "CircuitBreakerDecision",
    "CircuitBreakerKey",
    "CircuitBreakerSpec",
    "CircuitState",
    "InMemoryCircuitBreaker",
]

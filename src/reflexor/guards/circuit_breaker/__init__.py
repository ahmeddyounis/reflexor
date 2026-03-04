"""Circuit breaker primitives for execution guards.

This package provides a storage-agnostic Protocol and an in-memory implementation.
It is intentionally free of infrastructure/framework imports.
"""

from reflexor.guards.circuit_breaker.guard import (
    REASON_CIRCUIT_HALF_OPEN,
    REASON_CIRCUIT_OPEN,
    CircuitBreakerGuard,
)
from reflexor.guards.circuit_breaker.in_memory import InMemoryCircuitBreaker
from reflexor.guards.circuit_breaker.interface import CircuitBreaker
from reflexor.guards.circuit_breaker.key import CircuitBreakerKey
from reflexor.guards.circuit_breaker.resolver import extract_destination_hostname, key_for_tool_call
from reflexor.guards.circuit_breaker.spec import CircuitBreakerSpec
from reflexor.guards.circuit_breaker.types import CircuitBreakerDecision, CircuitState

__all__ = [
    "CircuitBreaker",
    "CircuitBreakerDecision",
    "CircuitBreakerGuard",
    "CircuitBreakerKey",
    "CircuitBreakerSpec",
    "CircuitState",
    "InMemoryCircuitBreaker",
    "REASON_CIRCUIT_HALF_OPEN",
    "REASON_CIRCUIT_OPEN",
    "extract_destination_hostname",
    "key_for_tool_call",
]

from __future__ import annotations

from typing import Protocol

from reflexor.guards.circuit_breaker.key import CircuitBreakerKey
from reflexor.guards.circuit_breaker.types import CircuitBreakerDecision


class CircuitBreaker(Protocol):
    async def allow_call(
        self,
        *,
        key: CircuitBreakerKey,
        now_s: float,
    ) -> CircuitBreakerDecision: ...

    async def record_result(
        self,
        *,
        key: CircuitBreakerKey,
        ok: bool,
        now_s: float,
    ) -> None: ...


__all__ = ["CircuitBreaker"]

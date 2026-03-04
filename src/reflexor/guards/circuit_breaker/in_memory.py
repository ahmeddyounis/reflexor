from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
from dataclasses import dataclass, field

from reflexor.guards.circuit_breaker.interface import CircuitBreaker
from reflexor.guards.circuit_breaker.key import CircuitBreakerKey
from reflexor.guards.circuit_breaker.spec import CircuitBreakerSpec
from reflexor.guards.circuit_breaker.types import CircuitBreakerDecision, CircuitState


@dataclass(slots=True)
class _Circuit:
    state: CircuitState = CircuitState.CLOSED
    failures: deque[float] = field(default_factory=deque)
    opened_at_s: float | None = None
    half_open_in_flight: int = 0
    half_open_successes: int = 0
    last_seen_s: float = 0.0


class InMemoryCircuitBreaker(CircuitBreaker):
    """In-memory circuit breaker with bounded memory (LRU + idle TTL eviction)."""

    def __init__(
        self,
        *,
        spec: CircuitBreakerSpec,
        max_keys: int = 10_000,
        ttl_s: float = 3600.0,
    ) -> None:
        max_keys_i = int(max_keys)
        ttl_f = float(ttl_s)
        if max_keys_i <= 0:
            raise ValueError("max_keys must be > 0")
        if ttl_f <= 0:
            raise ValueError("ttl_s must be > 0")

        self._spec = spec
        self._max_keys = max_keys_i
        self._ttl_s = ttl_f
        self._lock = asyncio.Lock()
        self._circuits: OrderedDict[CircuitBreakerKey, _Circuit] = OrderedDict()

    @property
    def spec(self) -> CircuitBreakerSpec:
        return self._spec

    @property
    def max_keys(self) -> int:
        return int(self._max_keys)

    @property
    def ttl_s(self) -> float:
        return float(self._ttl_s)

    @property
    def size(self) -> int:
        return len(self._circuits)

    def snapshot_keys(self) -> tuple[CircuitBreakerKey, ...]:
        """Return keys in LRU order (oldest first)."""

        return tuple(self._circuits.keys())

    def _is_expired(self, *, circuit: _Circuit, now_s: float) -> bool:
        return (float(now_s) - float(circuit.last_seen_s)) > float(self._ttl_s)

    def _evict_expired(self, *, now_s: float) -> None:
        while self._circuits:
            oldest_key = next(iter(self._circuits))
            circuit = self._circuits[oldest_key]
            if not self._is_expired(circuit=circuit, now_s=now_s):
                return
            self._circuits.popitem(last=False)

    def _evict_lru(self) -> None:
        while len(self._circuits) > int(self._max_keys):
            self._circuits.popitem(last=False)

    def _touch(self, *, key: CircuitBreakerKey, circuit: _Circuit, now_s: float) -> None:
        circuit.last_seen_s = float(now_s)
        self._circuits[key] = circuit
        self._circuits.move_to_end(key, last=True)

    def _get_or_create(self, *, key: CircuitBreakerKey, now_s: float) -> _Circuit:
        existing = self._circuits.get(key)
        if existing is not None:
            self._touch(key=key, circuit=existing, now_s=now_s)
            return existing
        circuit = _Circuit(last_seen_s=float(now_s))
        self._touch(key=key, circuit=circuit, now_s=now_s)
        return circuit

    def _prune_failures(self, circuit: _Circuit, *, now_s: float) -> None:
        cutoff = float(now_s) - float(self._spec.window_s)
        while circuit.failures and float(circuit.failures[0]) < cutoff:
            circuit.failures.popleft()

    def _open(self, circuit: _Circuit, *, now_s: float) -> None:
        circuit.state = CircuitState.OPEN
        circuit.opened_at_s = float(now_s)
        circuit.failures.clear()
        circuit.half_open_in_flight = 0
        circuit.half_open_successes = 0

    def _half_open(self, circuit: _Circuit) -> None:
        circuit.state = CircuitState.HALF_OPEN
        circuit.opened_at_s = None
        circuit.failures.clear()
        circuit.half_open_in_flight = 0
        circuit.half_open_successes = 0

    def _close(self, circuit: _Circuit) -> None:
        circuit.state = CircuitState.CLOSED
        circuit.opened_at_s = None
        circuit.failures.clear()
        circuit.half_open_in_flight = 0
        circuit.half_open_successes = 0

    async def allow_call(self, *, key: CircuitBreakerKey, now_s: float) -> CircuitBreakerDecision:
        now = float(now_s)
        if now < 0:
            raise ValueError("now_s must be >= 0")

        async with self._lock:
            self._evict_expired(now_s=now)
            circuit = self._get_or_create(key=key, now_s=now)
            self._evict_lru()

            self._prune_failures(circuit, now_s=now)

            if circuit.state == CircuitState.OPEN:
                assert circuit.opened_at_s is not None
                remaining = (float(circuit.opened_at_s) + float(self._spec.open_cooldown_s)) - now
                if remaining > 0:
                    return CircuitBreakerDecision(
                        allowed=False, state=CircuitState.OPEN, retry_after_s=float(remaining)
                    )
                self._half_open(circuit)

            if circuit.state == CircuitState.HALF_OPEN:
                permit_limit = int(self._spec.half_open_permit_limit)
                if circuit.half_open_in_flight >= permit_limit:
                    return CircuitBreakerDecision(
                        allowed=False, state=CircuitState.HALF_OPEN, retry_after_s=0.0
                    )
                circuit.half_open_in_flight += 1
                return CircuitBreakerDecision(allowed=True, state=CircuitState.HALF_OPEN)

            if circuit.state != CircuitState.CLOSED:  # pragma: no cover
                raise RuntimeError(f"unknown circuit state: {circuit.state!r}")

            if len(circuit.failures) >= int(self._spec.failure_threshold):
                self._open(circuit, now_s=now)
                return CircuitBreakerDecision(
                    allowed=False,
                    state=CircuitState.OPEN,
                    retry_after_s=float(self._spec.open_cooldown_s),
                )

            return CircuitBreakerDecision(allowed=True, state=CircuitState.CLOSED)

    async def record_result(self, *, key: CircuitBreakerKey, ok: bool, now_s: float) -> None:
        now = float(now_s)
        if now < 0:
            raise ValueError("now_s must be >= 0")

        async with self._lock:
            self._evict_expired(now_s=now)
            circuit = self._get_or_create(key=key, now_s=now)
            self._evict_lru()

            self._prune_failures(circuit, now_s=now)

            if circuit.state == CircuitState.HALF_OPEN:
                if circuit.half_open_in_flight > 0:
                    circuit.half_open_in_flight -= 1

                if not bool(ok):
                    self._open(circuit, now_s=now)
                    return

                circuit.half_open_successes += 1
                if circuit.half_open_successes >= int(self._spec.success_threshold):
                    self._close(circuit)
                return

            if circuit.state == CircuitState.OPEN:
                if not bool(ok):
                    circuit.opened_at_s = float(now)
                return

            if circuit.state != CircuitState.CLOSED:  # pragma: no cover
                raise RuntimeError(f"unknown circuit state: {circuit.state!r}")

            if bool(ok):
                return

            circuit.failures.append(float(now))
            self._prune_failures(circuit, now_s=now)
            if len(circuit.failures) >= int(self._spec.failure_threshold):
                self._open(circuit, now_s=now)


__all__ = ["InMemoryCircuitBreaker"]

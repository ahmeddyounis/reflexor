from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass

from reflexor.guards.circuit_breaker.interface import CircuitBreaker
from reflexor.guards.circuit_breaker.key import CircuitBreakerKey
from reflexor.guards.circuit_breaker.spec import CircuitBreakerSpec
from reflexor.guards.circuit_breaker.types import CircuitBreakerDecision, CircuitState
from reflexor.infra.guards.redis_circuit_breaker_lua import ALLOW_CALL_LUA, RECORD_RESULT_LUA
from reflexor.infra.redis import RedisEvalClient, import_redis_asyncio


def _canonical_key_json(key: CircuitBreakerKey) -> str:
    payload: dict[str, str] = {}
    if key.tool_name is not None:
        payload["tool_name"] = key.tool_name
    if key.destination is not None:
        payload["destination"] = key.destination
    if key.scope is not None:
        payload["scope"] = key.scope
    if key.signature is not None:
        payload["signature"] = key.signature

    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _redis_base_key(prefix: str, key: CircuitBreakerKey) -> str:
    digest = hashlib.sha256(_canonical_key_json(key).encode("utf-8")).hexdigest()
    return f"{prefix}:{digest}"


def _redis_keys(prefix: str, key: CircuitBreakerKey) -> tuple[str, str]:
    base_key = _redis_base_key(prefix, key)
    return f"{base_key}:state", f"{base_key}:failures"


@dataclass(frozen=True, slots=True)
class RedisCircuitBreakerConfig:
    redis_url: str
    key_prefix: str = "reflexor:circuit_breaker"
    ttl_s: float | None = 3600.0

    def __post_init__(self) -> None:
        url = str(self.redis_url).strip()
        if not url:
            raise ValueError("redis_url must be non-empty")
        prefix = str(self.key_prefix).strip()
        if not prefix:
            raise ValueError("key_prefix must be non-empty")

        if self.ttl_s is None:
            ttl_s: float | None = None
        else:
            ttl_s = float(self.ttl_s)
            if not math.isfinite(ttl_s) or ttl_s <= 0:
                raise ValueError("ttl_s must be finite and > 0 when set")

        object.__setattr__(self, "redis_url", url)
        object.__setattr__(self, "key_prefix", prefix)
        object.__setattr__(self, "ttl_s", ttl_s)


class RedisCircuitBreaker(CircuitBreaker):
    """Distributed circuit breaker backed by Redis (atomic state updates via Lua)."""

    def __init__(
        self,
        *,
        spec: CircuitBreakerSpec,
        config: RedisCircuitBreakerConfig,
        client: RedisEvalClient | None = None,
    ) -> None:
        self._spec = spec
        self._config = config
        if client is None:
            redis_asyncio = import_redis_asyncio()
            self._redis = redis_asyncio.Redis.from_url(
                config.redis_url,
                decode_responses=True,
            )
            self._owns_client = True
        else:
            self._redis = client
            self._owns_client = False

    @property
    def spec(self) -> CircuitBreakerSpec:
        return self._spec

    @property
    def config(self) -> RedisCircuitBreakerConfig:
        return self._config

    @staticmethod
    def _duration_ms(value_s: float, *, field_name: str, allow_zero: bool = False) -> int:
        value = float(value_s)
        if not math.isfinite(value):
            comparator = ">= 0" if allow_zero else "> 0"
            raise ValueError(f"{field_name} must be finite and {comparator}")
        if allow_zero:
            if value < 0:
                raise ValueError(f"{field_name} must be finite and >= 0")
            if value == 0:
                return 0
        elif value <= 0:
            raise ValueError(f"{field_name} must be finite and > 0")
        return max(1, math.ceil(value * 1000.0))

    async def aclose(self) -> None:
        if not self._owns_client:
            return
        await self._redis.aclose(close_connection_pool=True)

    def _ttl_ms(self) -> int:
        if self._config.ttl_s is None:
            return 0
        return self._duration_ms(self._config.ttl_s, field_name="ttl_s")

    async def allow_call(self, *, key: CircuitBreakerKey, now_s: float) -> CircuitBreakerDecision:
        now = float(now_s)
        if not math.isfinite(now) or now < 0:
            raise ValueError("now_s must be finite and >= 0")

        state_key, failures_key = _redis_keys(self._config.key_prefix, key)

        spec = self._spec
        response = await self._redis.eval(
            ALLOW_CALL_LUA,
            2,
            state_key,
            failures_key,
            str(int(now * 1000)),
            str(int(spec.failure_threshold)),
            str(self._duration_ms(spec.window_s, field_name="window_s")),
            str(
                self._duration_ms(
                    spec.open_cooldown_s,
                    field_name="open_cooldown_s",
                    allow_zero=True,
                )
            ),
            str(int(spec.half_open_max_calls)),
            str(int(spec.success_threshold)),
            str(int(self._ttl_ms())),
        )

        allowed_i = 0
        state_s = CircuitState.CLOSED.value
        retry_after_ms_i = -1
        if isinstance(response, (list, tuple)) and len(response) >= 3:
            try:
                allowed_i = int(response[0])
            except (TypeError, ValueError):
                allowed_i = 0

            if isinstance(response[1], str) and response[1].strip():
                state_s = response[1].strip()

            try:
                retry_after_ms_i = int(response[2])
            except (TypeError, ValueError):
                retry_after_ms_i = -1

        try:
            state = CircuitState(state_s)
        except ValueError:
            state = CircuitState.CLOSED

        retry_after_s: float | None
        if retry_after_ms_i < 0:
            retry_after_s = None
        else:
            retry_after_s = float(retry_after_ms_i) / 1000.0

        return CircuitBreakerDecision(
            allowed=allowed_i == 1,
            state=state,
            retry_after_s=retry_after_s,
        )

    async def record_result(self, *, key: CircuitBreakerKey, ok: bool, now_s: float) -> None:
        now = float(now_s)
        if not math.isfinite(now) or now < 0:
            raise ValueError("now_s must be finite and >= 0")

        state_key, failures_key = _redis_keys(self._config.key_prefix, key)

        spec = self._spec
        await self._redis.eval(
            RECORD_RESULT_LUA,
            2,
            state_key,
            failures_key,
            str(int(now * 1000)),
            "1" if bool(ok) else "0",
            str(int(spec.failure_threshold)),
            str(self._duration_ms(spec.window_s, field_name="window_s")),
            str(
                self._duration_ms(
                    spec.open_cooldown_s,
                    field_name="open_cooldown_s",
                    allow_zero=True,
                )
            ),
            str(int(spec.half_open_max_calls)),
            str(int(spec.success_threshold)),
            str(int(self._ttl_ms())),
        )


__all__ = ["RedisCircuitBreaker", "RedisCircuitBreakerConfig"]

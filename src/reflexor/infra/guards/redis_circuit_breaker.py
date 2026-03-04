from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
from dataclasses import dataclass
from typing import Any, Protocol

from reflexor.guards.circuit_breaker.interface import CircuitBreaker
from reflexor.guards.circuit_breaker.key import CircuitBreakerKey
from reflexor.guards.circuit_breaker.spec import CircuitBreakerSpec
from reflexor.guards.circuit_breaker.types import CircuitBreakerDecision, CircuitState

_STATE_FIELD = "state"
_OPENED_AT_MS_FIELD = "opened_at_ms"
_HALF_OPEN_IN_FLIGHT_FIELD = "half_open_in_flight"
_HALF_OPEN_SUCCESSES_FIELD = "half_open_successes"
_FAILURE_SEQ_FIELD = "failure_seq"

_ALLOW_CALL_LUA = f"""
local state_key = KEYS[1]
local failures_key = KEYS[2]

local now_ms = tonumber(ARGV[1])
local failure_threshold = tonumber(ARGV[2])
local window_ms = tonumber(ARGV[3])
local open_cooldown_ms = tonumber(ARGV[4])
local half_open_max_calls = tonumber(ARGV[5])
local success_threshold = tonumber(ARGV[6])
local ttl_ms = tonumber(ARGV[7])

local permit_limit = half_open_max_calls
if success_threshold < permit_limit then
  permit_limit = success_threshold
end

local raw = redis.call(
  'HMGET',
  state_key,
  '{_STATE_FIELD}',
  '{_OPENED_AT_MS_FIELD}',
  '{_HALF_OPEN_IN_FLIGHT_FIELD}',
  '{_HALF_OPEN_SUCCESSES_FIELD}'
)

local state = raw[1]
if state == false or state == nil or state == '' then
  state = '{CircuitState.CLOSED.value}'
end

local opened_at_ms = tonumber(raw[2])
local in_flight = tonumber(raw[3]) or 0
local successes = tonumber(raw[4]) or 0

local cutoff = now_ms - window_ms
if cutoff < 0 then cutoff = 0 end
redis.call('ZREMRANGEBYSCORE', failures_key, 0, cutoff)

local allowed = 1
local out_state = state
local retry_after_ms = -1

if state == '{CircuitState.OPEN.value}' then
  if opened_at_ms == nil then
    opened_at_ms = now_ms
  end
  local remaining = (opened_at_ms + open_cooldown_ms) - now_ms
  if remaining > 0 then
    allowed = 0
    out_state = '{CircuitState.OPEN.value}'
    retry_after_ms = remaining
  else
    out_state = '{CircuitState.HALF_OPEN.value}'
    opened_at_ms = nil
    in_flight = 0
    successes = 0
    redis.call('DEL', failures_key)
  end
end

if out_state == '{CircuitState.HALF_OPEN.value}' then
  if in_flight >= permit_limit then
    allowed = 0
    retry_after_ms = 0
  else
    allowed = 1
    in_flight = in_flight + 1
  end
end

if out_state == '{CircuitState.CLOSED.value}' then
  local count = redis.call('ZCARD', failures_key)
  if count >= failure_threshold then
    allowed = 0
    out_state = '{CircuitState.OPEN.value}'
    opened_at_ms = now_ms
    in_flight = 0
    successes = 0
    retry_after_ms = open_cooldown_ms
    redis.call('DEL', failures_key)
  else
    allowed = 1
    retry_after_ms = -1
  end
end

redis.call(
  'HSET',
  state_key,
  '{_STATE_FIELD}', out_state,
  '{_HALF_OPEN_IN_FLIGHT_FIELD}', in_flight,
  '{_HALF_OPEN_SUCCESSES_FIELD}', successes
)

if out_state == '{CircuitState.OPEN.value}' then
  redis.call('HSET', state_key, '{_OPENED_AT_MS_FIELD}', opened_at_ms)
else
  redis.call('HDEL', state_key, '{_OPENED_AT_MS_FIELD}')
end

if ttl_ms > 0 then
  redis.call('PEXPIRE', state_key, ttl_ms)
  redis.call('PEXPIRE', failures_key, ttl_ms)
end

return {{allowed, out_state, retry_after_ms}}
"""

_RECORD_RESULT_LUA = f"""
local state_key = KEYS[1]
local failures_key = KEYS[2]

local now_ms = tonumber(ARGV[1])
local ok = tonumber(ARGV[2])
local failure_threshold = tonumber(ARGV[3])
local window_ms = tonumber(ARGV[4])
local open_cooldown_ms = tonumber(ARGV[5])
local half_open_max_calls = tonumber(ARGV[6])
local success_threshold = tonumber(ARGV[7])
local ttl_ms = tonumber(ARGV[8])

local raw = redis.call(
  'HMGET',
  state_key,
  '{_STATE_FIELD}',
  '{_OPENED_AT_MS_FIELD}',
  '{_HALF_OPEN_IN_FLIGHT_FIELD}',
  '{_HALF_OPEN_SUCCESSES_FIELD}'
)

local state = raw[1]
if state == false or state == nil or state == '' then
  state = '{CircuitState.CLOSED.value}'
end

local opened_at_ms = tonumber(raw[2])
local in_flight = tonumber(raw[3]) or 0
local successes = tonumber(raw[4]) or 0

local cutoff = now_ms - window_ms
if cutoff < 0 then cutoff = 0 end
redis.call('ZREMRANGEBYSCORE', failures_key, 0, cutoff)

if state == '{CircuitState.HALF_OPEN.value}' then
  if in_flight > 0 then
    in_flight = in_flight - 1
  end

  if ok == 0 then
    state = '{CircuitState.OPEN.value}'
    opened_at_ms = now_ms
    in_flight = 0
    successes = 0
    redis.call('DEL', failures_key)
  else
    successes = successes + 1
    if successes >= success_threshold then
      state = '{CircuitState.CLOSED.value}'
      opened_at_ms = nil
      in_flight = 0
      successes = 0
      redis.call('DEL', failures_key)
    end
  end
elseif state == '{CircuitState.OPEN.value}' then
  if ok == 0 then
    opened_at_ms = now_ms
  end
elseif state == '{CircuitState.CLOSED.value}' then
  if ok == 0 then
    local seq = redis.call('HINCRBY', state_key, '{_FAILURE_SEQ_FIELD}', 1)
    local member = tostring(now_ms) .. ':' .. tostring(seq)
    redis.call('ZADD', failures_key, now_ms, member)
    redis.call('ZREMRANGEBYSCORE', failures_key, 0, cutoff)
    local count = redis.call('ZCARD', failures_key)
    if count >= failure_threshold then
      state = '{CircuitState.OPEN.value}'
      opened_at_ms = now_ms
      in_flight = 0
      successes = 0
      redis.call('DEL', failures_key)
    end
  end
else
  state = '{CircuitState.CLOSED.value}'
  opened_at_ms = nil
  in_flight = 0
  successes = 0
end

redis.call(
  'HSET',
  state_key,
  '{_STATE_FIELD}', state,
  '{_HALF_OPEN_IN_FLIGHT_FIELD}', in_flight,
  '{_HALF_OPEN_SUCCESSES_FIELD}', successes
)

if state == '{CircuitState.OPEN.value}' then
  redis.call('HSET', state_key, '{_OPENED_AT_MS_FIELD}', opened_at_ms)
else
  redis.call('HDEL', state_key, '{_OPENED_AT_MS_FIELD}')
end

if ttl_ms > 0 then
  redis.call('PEXPIRE', state_key, ttl_ms)
  redis.call('PEXPIRE', failures_key, ttl_ms)
end

return 1
"""


def _import_redis_asyncio() -> Any:
    if importlib.util.find_spec("redis") is None:
        raise RuntimeError(
            "Missing optional dependency redis.\n"
            "- If working from the repo: pip install -e '.[redis]'\n"
            "- If installing the package: pip install 'reflexor[redis]'"
        )
    return importlib.import_module("redis.asyncio")


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


def _redis_state_key(prefix: str, key: CircuitBreakerKey) -> str:
    return f"{_redis_base_key(prefix, key)}:state"


def _redis_failures_key(prefix: str, key: CircuitBreakerKey) -> str:
    return f"{_redis_base_key(prefix, key)}:failures"


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
            if ttl_s <= 0:
                raise ValueError("ttl_s must be > 0 when set")

        object.__setattr__(self, "redis_url", url)
        object.__setattr__(self, "key_prefix", prefix)
        object.__setattr__(self, "ttl_s", ttl_s)


class RedisClient(Protocol):
    async def eval(self, script: str, numkeys: int, *keys_and_args: str) -> Any: ...

    async def aclose(self, *, close_connection_pool: bool = ...) -> None: ...


class RedisCircuitBreaker(CircuitBreaker):
    """Distributed circuit breaker backed by Redis (atomic state updates via Lua)."""

    def __init__(
        self,
        *,
        spec: CircuitBreakerSpec,
        config: RedisCircuitBreakerConfig,
        client: RedisClient | None = None,
    ) -> None:
        self._spec = spec
        self._config = config
        if client is None:
            redis_asyncio = _import_redis_asyncio()
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

    async def aclose(self) -> None:
        if not self._owns_client:
            return
        await self._redis.aclose(close_connection_pool=True)

    def _ttl_ms(self) -> int:
        if self._config.ttl_s is None:
            return 0
        return int(float(self._config.ttl_s) * 1000)

    async def allow_call(self, *, key: CircuitBreakerKey, now_s: float) -> CircuitBreakerDecision:
        now = float(now_s)
        if now < 0:
            raise ValueError("now_s must be >= 0")

        state_key = _redis_state_key(self._config.key_prefix, key)
        failures_key = _redis_failures_key(self._config.key_prefix, key)

        spec = self._spec
        response = await self._redis.eval(
            _ALLOW_CALL_LUA,
            2,
            state_key,
            failures_key,
            str(int(now * 1000)),
            str(int(spec.failure_threshold)),
            str(int(float(spec.window_s) * 1000)),
            str(int(float(spec.open_cooldown_s) * 1000)),
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
        if now < 0:
            raise ValueError("now_s must be >= 0")

        state_key = _redis_state_key(self._config.key_prefix, key)
        failures_key = _redis_failures_key(self._config.key_prefix, key)

        spec = self._spec
        await self._redis.eval(
            _RECORD_RESULT_LUA,
            2,
            state_key,
            failures_key,
            str(int(now * 1000)),
            "1" if bool(ok) else "0",
            str(int(spec.failure_threshold)),
            str(int(float(spec.window_s) * 1000)),
            str(int(float(spec.open_cooldown_s) * 1000)),
            str(int(spec.half_open_max_calls)),
            str(int(spec.success_threshold)),
            str(int(self._ttl_ms())),
        )


__all__ = ["RedisCircuitBreaker", "RedisCircuitBreakerConfig"]

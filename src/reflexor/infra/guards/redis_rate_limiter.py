from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
from dataclasses import dataclass
from typing import Any, Protocol

from reflexor.guards.rate_limit.key import RateLimitKey
from reflexor.guards.rate_limit.limiter import RateLimiter, RateLimitResult
from reflexor.guards.rate_limit.spec import RateLimitSpec

_TOKENS_FIELD = "tokens"
_LAST_REFILL_MS_FIELD = "last_refill_ms"

_CONSUME_LUA = f"""
local key = KEYS[1]
local now_ms = tonumber(ARGV[1])
local max_tokens = tonumber(ARGV[2])
local refill_rate_per_s = tonumber(ARGV[3])
local cost = tonumber(ARGV[4])
local ttl_ms = tonumber(ARGV[5])

local raw = redis.call('HMGET', key, '{_TOKENS_FIELD}', '{_LAST_REFILL_MS_FIELD}')
local tokens = tonumber(raw[1])
local last_refill_ms = tonumber(raw[2])

if tokens == nil or last_refill_ms == nil then
  tokens = max_tokens
  last_refill_ms = now_ms
end

local elapsed_ms = now_ms - last_refill_ms
if elapsed_ms < 0 then elapsed_ms = 0 end

if elapsed_ms > 0 and refill_rate_per_s > 0 then
  tokens = math.min(max_tokens, tokens + (elapsed_ms / 1000.0) * refill_rate_per_s)
end

last_refill_ms = now_ms

local allowed = 0
local retry_after_ms = 0

if cost > max_tokens then
  allowed = 0
  retry_after_ms = -1
elseif cost <= tokens then
  allowed = 1
  tokens = tokens - cost
  retry_after_ms = 0
else
  allowed = 0
  if refill_rate_per_s <= 0 then
    retry_after_ms = -1
  else
    local deficit = cost - tokens
    retry_after_ms = math.ceil((deficit / refill_rate_per_s) * 1000.0)
  end
end

redis.call('HSET', key, '{_TOKENS_FIELD}', tokens, '{_LAST_REFILL_MS_FIELD}', last_refill_ms)
if ttl_ms > 0 then
  redis.call('PEXPIRE', key, ttl_ms)
end

return {{allowed, retry_after_ms}}
"""


def _import_redis_asyncio() -> Any:
    if importlib.util.find_spec("redis") is None:
        raise RuntimeError(
            "Missing optional dependency redis.\n"
            "- If working from the repo: pip install -e '.[redis]'\n"
            "- If installing the package: pip install 'reflexor[redis]'"
        )
    return importlib.import_module("redis.asyncio")


def _canonical_key_json(key: RateLimitKey) -> str:
    payload: dict[str, str] = {}
    if key.scope is not None:
        payload["scope"] = key.scope
    if key.tool_name is not None:
        payload["tool_name"] = key.tool_name
    if key.destination is not None:
        payload["destination"] = key.destination
    if key.run_id is not None:
        payload["run_id"] = key.run_id

    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _redis_key(prefix: str, key: RateLimitKey) -> str:
    digest = hashlib.sha256(_canonical_key_json(key).encode("utf-8")).hexdigest()
    return f"{prefix}:{digest}"


@dataclass(frozen=True, slots=True)
class RedisRateLimiterConfig:
    redis_url: str
    key_prefix: str = "reflexor:rate_limit"
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


class RedisRateLimiter(RateLimiter):
    """Distributed RateLimiter backed by Redis (atomic consume via Lua)."""

    def __init__(
        self,
        *,
        config: RedisRateLimiterConfig,
        client: RedisClient | None = None,
    ) -> None:
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
    def config(self) -> RedisRateLimiterConfig:
        return self._config

    async def aclose(self) -> None:
        if not self._owns_client:
            return
        await self._redis.aclose(close_connection_pool=True)

    async def consume(
        self,
        *,
        key: RateLimitKey,
        spec: RateLimitSpec,
        cost: float,
        now_s: float,
    ) -> RateLimitResult:
        now = float(now_s)
        if now < 0:
            raise ValueError("now_s must be >= 0")

        redis_key = _redis_key(self._config.key_prefix, key)
        now_ms = int(now * 1000)
        ttl_ms = 0
        if self._config.ttl_s is not None:
            ttl_ms = int(float(self._config.ttl_s) * 1000)

        response = await self._redis.eval(
            _CONSUME_LUA,
            1,
            redis_key,
            str(int(now_ms)),
            str(float(spec.max_tokens)),
            str(float(spec.refill_rate_per_s)),
            str(float(cost)),
            str(int(ttl_ms)),
        )

        allowed_i = 0
        retry_after_ms_i = -1
        if isinstance(response, (list, tuple)) and len(response) >= 2:
            try:
                allowed_i = int(response[0])
            except (TypeError, ValueError):
                allowed_i = 0
            try:
                retry_after_ms_i = int(response[1])
            except (TypeError, ValueError):
                retry_after_ms_i = -1

        allowed = allowed_i == 1
        if allowed:
            return RateLimitResult(allowed=True, retry_after_s=0.0)

        if retry_after_ms_i < 0:
            return RateLimitResult(allowed=False, retry_after_s=None)

        return RateLimitResult(allowed=False, retry_after_s=float(retry_after_ms_i) / 1000.0)


__all__ = ["RedisRateLimiter", "RedisRateLimiterConfig"]

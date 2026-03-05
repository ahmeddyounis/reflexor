from __future__ import annotations

from reflexor.guards.circuit_breaker.types import CircuitState

_STATE_FIELD = "state"
_OPENED_AT_MS_FIELD = "opened_at_ms"
_HALF_OPEN_IN_FLIGHT_FIELD = "half_open_in_flight"
_HALF_OPEN_SUCCESSES_FIELD = "half_open_successes"
_FAILURE_SEQ_FIELD = "failure_seq"

ALLOW_CALL_LUA = f"""
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

RECORD_RESULT_LUA = f"""
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

__all__ = ["ALLOW_CALL_LUA", "RECORD_RESULT_LUA"]

from __future__ import annotations

_PROMOTE_DELAYED_LUA = """
local delayed_key = KEYS[1]
local stream_key = KEYS[2]

local now_ms = tonumber(ARGV[1])
local count = tonumber(ARGV[2])
local field_name = ARGV[3]
local maxlen = ARGV[4]

local due = redis.call('ZRANGEBYSCORE', delayed_key, '-inf', now_ms, 'LIMIT', 0, count)

local moved = 0
for _, payload in ipairs(due) do
  local removed = redis.call('ZREM', delayed_key, payload)
  if removed == 1 then
    if maxlen ~= '' then
      redis.call('XADD', stream_key, 'MAXLEN', '~', tonumber(maxlen), '*', field_name, payload)
    else
      redis.call('XADD', stream_key, '*', field_name, payload)
    end
    moved = moved + 1
  end
end

return moved
"""

_ACK_AND_REQUEUE_LUA = """
local stream_key = KEYS[1]
local delayed_key = KEYS[2]

local group = ARGV[1]
local message_id = ARGV[2]
local payload = ARGV[3]
local available_at_ms = tonumber(ARGV[4])
local enqueue_immediate = ARGV[5]
local field_name = ARGV[6]
local maxlen = ARGV[7]

local acked = redis.call('XACK', stream_key, group, message_id)
if acked == 0 then
  return ''
end

if enqueue_immediate == '1' then
  if maxlen ~= '' then
    return redis.call('XADD', stream_key, 'MAXLEN', '~', tonumber(maxlen), '*', field_name, payload)
  else
    return redis.call('XADD', stream_key, '*', field_name, payload)
  end
end

redis.call('ZADD', delayed_key, available_at_ms, payload)
return ''
"""

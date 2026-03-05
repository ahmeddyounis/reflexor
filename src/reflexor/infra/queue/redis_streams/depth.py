from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from reflexor.infra.queue.redis_streams.core import RedisStreamsQueue


async def queue_depth(queue: RedisStreamsQueue) -> int:
    try:
        groups = await queue._redis.xinfo_groups(queue._stream_key)
    except Exception:
        groups = []

    pending = 0
    lag = 0
    for group in groups or []:
        if not isinstance(group, dict):
            continue
        name = group.get("name")
        if name != queue._group:
            continue
        try:
            pending = int(group.get("pending") or 0)
        except (TypeError, ValueError):
            pending = 0
        raw_lag = group.get("lag")
        if raw_lag is None:
            lag = 0
        else:
            try:
                lag = int(raw_lag)
            except (TypeError, ValueError):
                lag = 0
        break

    delayed = int(await queue._redis.zcard(queue._delayed_zset_key))
    return int(pending + lag + delayed)

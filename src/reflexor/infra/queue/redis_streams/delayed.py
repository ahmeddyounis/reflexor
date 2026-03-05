from __future__ import annotations

from typing import TYPE_CHECKING

from reflexor.infra.queue.redis_streams.codec import _FIELD_ENVELOPE
from reflexor.infra.queue.redis_streams.lua import _PROMOTE_DELAYED_LUA

if TYPE_CHECKING:
    from reflexor.infra.queue.redis_streams.core import RedisStreamsQueue


async def promote_delayed(queue: RedisStreamsQueue, *, now_ms: int) -> None:
    maxlen = "" if queue._stream_maxlen is None else str(int(queue._stream_maxlen))
    await queue._redis.eval(
        _PROMOTE_DELAYED_LUA,
        2,
        queue._delayed_zset_key,
        queue._stream_key,
        str(int(now_ms)),
        str(int(queue._promote_batch_size)),
        _FIELD_ENVELOPE,
        maxlen,
    )

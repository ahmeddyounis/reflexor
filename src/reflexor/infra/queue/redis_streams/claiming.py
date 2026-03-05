from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from reflexor.infra.queue.redis_streams.redis_helpers import (
    _extract_autoclaim_response,
    _is_unknown_command_error,
)

if TYPE_CHECKING:
    from reflexor.infra.queue.redis_streams.core import RedisStreamsQueue


async def try_claim_expired(
    queue: RedisStreamsQueue,
    *,
    claim_idle_ms: int,
) -> tuple[str, dict[str, str]] | None:
    try:
        return queue._claimed_buffer.get_nowait()
    except asyncio.QueueEmpty:
        pass

    async with queue._autoclaim_lock:
        try:
            return queue._claimed_buffer.get_nowait()
        except asyncio.QueueEmpty:
            pass

        if queue._autoclaim_supported is not False:
            try:
                response = await queue._redis.xautoclaim(
                    queue._stream_key,
                    queue._group,
                    queue._consumer,
                    min_idle_time=int(claim_idle_ms),
                    start_id=queue._autoclaim_start_id,
                    count=int(queue._claim_batch_size),
                    justid=False,
                )
            except Exception as exc:
                if _is_unknown_command_error(exc, command="XAUTOCLAIM"):
                    queue._autoclaim_supported = False
                else:
                    raise
            else:
                queue._autoclaim_supported = True
                next_start, entries = _extract_autoclaim_response(response)
                queue._autoclaim_start_id = next_start
                for entry in entries:
                    queue._claimed_buffer.put_nowait(entry)

        if queue._claimed_buffer.empty() and queue._autoclaim_supported is False:
            await try_claim_expired_fallback(queue, claim_idle_ms=int(claim_idle_ms))

    try:
        return queue._claimed_buffer.get_nowait()
    except asyncio.QueueEmpty:
        return None


async def try_claim_expired_fallback(queue: RedisStreamsQueue, *, claim_idle_ms: int) -> None:
    pending = await queue._redis.execute_command(
        "XPENDING",
        queue._stream_key,
        queue._group,
        "-",
        "+",
        int(queue._claim_batch_size),
    )

    candidate_ids: list[str] = []
    for entry in pending or []:
        if not isinstance(entry, (list, tuple)) or len(entry) < 3:
            continue
        message_id = entry[0]
        idle_ms = entry[2]
        try:
            if int(idle_ms) < int(claim_idle_ms):
                continue
        except (TypeError, ValueError):
            continue
        candidate_ids.append(message_id if isinstance(message_id, str) else str(message_id))

    if not candidate_ids:
        return

    claimed = await queue._redis.execute_command(
        "XCLAIM",
        queue._stream_key,
        queue._group,
        queue._consumer,
        int(claim_idle_ms),
        *candidate_ids,
    )

    for item in claimed or []:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue

        raw_id, raw_fields = item
        message_id = raw_id if isinstance(raw_id, str) else str(raw_id)

        fields: dict[str, str] = {}
        if isinstance(raw_fields, dict):
            for key, value in raw_fields.items():
                fields[key if isinstance(key, str) else str(key)] = (
                    value if isinstance(value, str) else str(value)
                )
        elif isinstance(raw_fields, (list, tuple)):
            for i in range(0, len(raw_fields), 2):
                if i + 1 >= len(raw_fields):
                    break
                key = raw_fields[i]
                value = raw_fields[i + 1]
                fields[key if isinstance(key, str) else str(key)] = (
                    value if isinstance(value, str) else str(value)
                )

        queue._claimed_buffer.put_nowait((message_id, fields))

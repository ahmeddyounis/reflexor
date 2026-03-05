from __future__ import annotations

import asyncio


class _StreamLimitExceeded(RuntimeError):
    def __init__(self, stream_name: str, *, limit_bytes: int) -> None:
        super().__init__(f"{stream_name} exceeded limit_bytes={limit_bytes}")
        self.stream_name = stream_name
        self.limit_bytes = int(limit_bytes)


async def _read_stream_limited(
    stream: asyncio.StreamReader,
    *,
    limit_bytes: int,
    stream_name: str,
    chunk_size: int = 16_384,
) -> bytes:
    limit = int(limit_bytes)
    if limit <= 0:
        raise ValueError("limit_bytes must be > 0")

    data = bytearray()
    while True:
        chunk = await stream.read(chunk_size)
        if not chunk:
            break
        data.extend(chunk)
        if len(data) > limit:
            raise _StreamLimitExceeded(stream_name, limit_bytes=limit)
    return bytes(data)

"""Infrastructure helpers for Redis-backed adapters.

This module centralizes optional dependency loading so individual adapters can remain
focused on their behavior and share consistent error messages.
"""

from __future__ import annotations

import importlib
import importlib.util
from typing import Any, Protocol


class RedisEvalClient(Protocol):
    async def eval(self, script: str, numkeys: int, *keys_and_args: str) -> Any: ...

    async def aclose(self, *, close_connection_pool: bool = ...) -> None: ...


def import_redis_asyncio() -> Any:
    if importlib.util.find_spec("redis") is None:
        raise RuntimeError(
            "Missing optional dependency redis.\n"
            "- If working from the repo: pip install -e '.[redis]'\n"
            "- If installing the package: pip install 'reflexor[redis]'"
        )
    return importlib.import_module("redis.asyncio")


__all__ = ["RedisEvalClient", "import_redis_asyncio"]

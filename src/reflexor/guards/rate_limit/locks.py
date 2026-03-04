from __future__ import annotations

import asyncio
from types import TracebackType
from typing import Protocol

from reflexor.guards.rate_limit.key import RateLimitKey


class AsyncLock(Protocol):
    async def __aenter__(self) -> None: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool | None: ...


class KeyedLockStrategy(Protocol):
    def lock(self, key: RateLimitKey) -> AsyncLock: ...


class _NoopAsyncLock:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool | None:
        return None


class NoopKeyedLockStrategy:
    def lock(self, key: RateLimitKey) -> AsyncLock:
        _ = key
        return _NoopAsyncLock()


class _AsyncioLockCtx:
    def __init__(self, lock: asyncio.Lock) -> None:
        self._lock = lock

    async def __aenter__(self) -> None:
        await self._lock.acquire()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool | None:
        self._lock.release()
        return None


class AsyncioKeyedLockStrategy:
    """Keyed lock strategy built on `asyncio.Lock`."""

    def __init__(self) -> None:
        self._locks: dict[RateLimitKey, asyncio.Lock] = {}
        self._guard = asyncio.Lock()

    async def _get_lock(self, key: RateLimitKey) -> asyncio.Lock:
        async with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[key] = lock
            return lock

    def lock(self, key: RateLimitKey) -> AsyncLock:
        # Async context manager that resolves the keyed lock lazily.
        strategy = self

        class _Ctx:
            def __init__(self) -> None:
                self._lock: asyncio.Lock | None = None

            async def __aenter__(self) -> None:
                self._lock = await strategy._get_lock(key)
                await self._lock.acquire()

            async def __aexit__(
                self,
                exc_type: type[BaseException] | None,
                exc: BaseException | None,
                tb: TracebackType | None,
            ) -> bool | None:
                if self._lock is not None:
                    self._lock.release()
                return None

        return _Ctx()


__all__ = [
    "AsyncLock",
    "AsyncioKeyedLockStrategy",
    "KeyedLockStrategy",
    "NoopKeyedLockStrategy",
]

from __future__ import annotations

import asyncio
from collections import defaultdict

import pytest

from reflexor.executor.concurrency import ConcurrencyLimiter


class _AsyncBarrier:
    def __init__(self, parties: int) -> None:
        if parties <= 0:
            raise ValueError("parties must be > 0")
        self._parties = int(parties)
        self._count = 0
        self._event = asyncio.Event()
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            self._count += 1
            if self._count >= self._parties:
                self._event.set()
        await self._event.wait()


async def _cancel_tasks(tasks: list[asyncio.Task[object]]) -> None:
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_concurrency_limiter_enforces_global_and_per_tool_limits_with_barrier() -> None:
    max_global = 3
    per_tool = {"tool.a": 2, "tool.b": 1}
    limiter = ConcurrencyLimiter(max_global=max_global, per_tool=per_tool)

    start = _AsyncBarrier(parties=40)
    hold = asyncio.Event()
    lock = asyncio.Lock()

    in_flight_total = 0
    in_flight_by_tool: dict[str, int] = defaultdict(int)
    max_seen_total = 0
    max_seen_by_tool: dict[str, int] = defaultdict(int)

    saturated = asyncio.Event()

    async def worker(tool_name: str) -> None:
        nonlocal in_flight_total, max_seen_total
        await start.wait()
        async with limiter.limit(tool_name):
            async with lock:
                in_flight_total += 1
                in_flight_by_tool[tool_name] += 1

                max_seen_total = max(max_seen_total, in_flight_total)
                max_seen_by_tool[tool_name] = max(
                    max_seen_by_tool[tool_name], in_flight_by_tool[tool_name]
                )

                assert in_flight_total <= max_global
                assert in_flight_by_tool[tool_name] <= per_tool[tool_name]

                if in_flight_total == max_global:
                    saturated.set()

            await hold.wait()

            async with lock:
                in_flight_total -= 1
                in_flight_by_tool[tool_name] -= 1

    tasks: list[asyncio.Task[object]] = []
    try:
        for _ in range(20):
            tasks.append(asyncio.create_task(worker("tool.a")))
        for _ in range(20):
            tasks.append(asyncio.create_task(worker("tool.b")))

        await asyncio.wait_for(saturated.wait(), timeout=1.0)
        hold.set()
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=1.0)
    except TimeoutError:  # pragma: no cover
        await _cancel_tasks(tasks)
        raise

    assert max_seen_total == max_global
    assert max_seen_by_tool["tool.a"] == per_tool["tool.a"]
    assert max_seen_by_tool["tool.b"] == per_tool["tool.b"]

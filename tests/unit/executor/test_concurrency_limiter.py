from __future__ import annotations

import asyncio

import pytest

from reflexor.executor.concurrency import ConcurrencyLimiter


@pytest.mark.asyncio
async def test_concurrency_limiter_respects_global_limit() -> None:
    limiter = ConcurrencyLimiter(max_global=2)
    hold = asyncio.Event()
    lock = asyncio.Lock()

    current = 0
    max_seen = 0
    two_entered = asyncio.Event()

    async def worker() -> None:
        nonlocal current, max_seen
        async with limiter.limit("mock.echo"):
            async with lock:
                current += 1
                max_seen = max(max_seen, current)
                assert current <= 2
                if current == 2:
                    two_entered.set()
            await hold.wait()
            async with lock:
                current -= 1

    tasks = [asyncio.create_task(worker()) for _ in range(5)]
    await asyncio.wait_for(two_entered.wait(), timeout=1.0)
    hold.set()
    await asyncio.wait_for(asyncio.gather(*tasks), timeout=1.0)

    assert max_seen == 2


@pytest.mark.asyncio
async def test_concurrency_limiter_respects_per_tool_limit() -> None:
    limiter = ConcurrencyLimiter(max_global=10, per_tool={"mock.echo": 1})
    hold = asyncio.Event()
    lock = asyncio.Lock()

    current = 0
    max_seen = 0
    first_entered = asyncio.Event()

    async def worker() -> None:
        nonlocal current, max_seen
        async with limiter.limit("mock.echo"):
            async with lock:
                current += 1
                max_seen = max(max_seen, current)
                assert current <= 1
                first_entered.set()
            await hold.wait()
            async with lock:
                current -= 1

    tasks = [asyncio.create_task(worker()) for _ in range(3)]
    await asyncio.wait_for(first_entered.wait(), timeout=1.0)
    hold.set()
    await asyncio.wait_for(asyncio.gather(*tasks), timeout=1.0)

    assert max_seen == 1


@pytest.mark.asyncio
async def test_concurrency_limiter_global_and_per_tool_do_not_deadlock() -> None:
    limiter = ConcurrencyLimiter(max_global=2, per_tool={"a": 1, "b": 1})
    hold = asyncio.Event()

    async def worker(name: str) -> None:
        async with limiter.limit(name):
            await hold.wait()

    tasks = [
        asyncio.create_task(worker("a")),
        asyncio.create_task(worker("b")),
        asyncio.create_task(worker("a")),
        asyncio.create_task(worker("b")),
    ]

    await asyncio.sleep(0)
    hold.set()
    await asyncio.wait_for(asyncio.gather(*tasks), timeout=1.0)


@pytest.mark.asyncio
async def test_concurrency_limiter_does_not_starve_other_tools_behind_saturated_tool() -> None:
    limiter = ConcurrencyLimiter(max_global=2, per_tool={"a": 1, "b": 1})
    hold_a = asyncio.Event()
    a_entered = asyncio.Event()
    b_entered = asyncio.Event()

    async def worker_a() -> None:
        async with limiter.limit("a"):
            a_entered.set()
            await hold_a.wait()

    async def worker_a_waiting() -> None:
        await a_entered.wait()
        async with limiter.limit("a"):
            return None

    async def worker_b() -> None:
        await a_entered.wait()
        async with limiter.limit("b"):
            b_entered.set()

    task_a = asyncio.create_task(worker_a())
    await asyncio.wait_for(a_entered.wait(), timeout=1.0)

    task_a_waiting = asyncio.create_task(worker_a_waiting())
    await asyncio.sleep(0)

    task_b = asyncio.create_task(worker_b())
    await asyncio.wait_for(b_entered.wait(), timeout=1.0)

    hold_a.set()
    await asyncio.wait_for(asyncio.gather(task_a, task_a_waiting, task_b), timeout=1.0)

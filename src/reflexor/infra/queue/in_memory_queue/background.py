from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import TYPE_CHECKING

from reflexor.infra.queue.in_memory_queue.leases import expire_leases
from reflexor.infra.queue.in_memory_queue.state import promote_delayed

if TYPE_CHECKING:
    from reflexor.infra.queue.in_memory_queue.core import InMemoryQueue


def ensure_background_tasks_started(queue: InMemoryQueue) -> None:
    if queue._delayed_promoter_task is not None and queue._lease_reaper_task is not None:
        return
    if queue._closed:
        return
    loop = asyncio.get_running_loop()
    if queue._delayed_promoter_task is None:
        queue._delayed_promoter_task = loop.create_task(delayed_promoter_loop(queue))
    if queue._lease_reaper_task is None:
        queue._lease_reaper_task = loop.create_task(lease_reaper_loop(queue))


async def delayed_promoter_loop(queue: InMemoryQueue) -> None:
    try:
        while True:
            async with queue._lock:
                if queue._closed:
                    return
                now = int(queue._now_ms())
                promote_delayed(queue, now=now)
                next_due = queue._delayed[0][0] if queue._delayed else None

            await sleep_until_next(queue, now_ms=now, next_ms=next_due)
    except asyncio.CancelledError:
        return


async def lease_reaper_loop(queue: InMemoryQueue) -> None:
    try:
        while True:
            redeliver = []
            async with queue._lock:
                if queue._closed:
                    return
                now = int(queue._now_ms())
                redeliver = expire_leases(queue, now=now)
                next_deadline = queue._lease_deadlines[0][0] if queue._lease_deadlines else None

            for observation in redeliver:
                queue._observer.on_redeliver(observation)

            await sleep_until_next(queue, now_ms=now, next_ms=next_deadline)
    except asyncio.CancelledError:
        return


async def sleep_until_next(queue: InMemoryQueue, *, now_ms: int, next_ms: int | None) -> None:
    queue._wakeup_event.clear()
    if next_ms is None:
        timeout_s = 0.25
    else:
        timeout_s = max(0.0, (next_ms - now_ms) / 1000)
        timeout_s = min(timeout_s, 0.25)

    try:
        await asyncio.wait_for(queue._wakeup_event.wait(), timeout=timeout_s)
    except TimeoutError:
        return


async def cancel_background_tasks(queue: InMemoryQueue) -> None:
    tasks: list[asyncio.Task[None]] = []
    if queue._delayed_promoter_task is not None:
        tasks.append(queue._delayed_promoter_task)
    if queue._lease_reaper_task is not None:
        tasks.append(queue._lease_reaper_task)

    for task in tasks:
        task.cancel()
    if tasks:
        with suppress(asyncio.CancelledError):
            await asyncio.gather(*tasks, return_exceptions=True)

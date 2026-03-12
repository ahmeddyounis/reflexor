from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from reflexor.orchestrator.clock import Clock
from reflexor.orchestrator.triggers import DebouncedTrigger, PeriodicTicker


@dataclass(slots=True)
class _ManualClock(Clock):
    now_ms_value: int = 0
    monotonic_ms_value: int = 0
    _condition: asyncio.Condition = field(default_factory=asyncio.Condition)

    def now_ms(self) -> int:
        return self.now_ms_value

    def monotonic_ms(self) -> int:
        return self.monotonic_ms_value

    async def sleep(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("sleep seconds must be >= 0")
        delta_ms = int(float(seconds) * 1000)
        async with self._condition:
            target = self.monotonic_ms_value + delta_ms
            await self._condition.wait_for(lambda: self.monotonic_ms_value >= target)

    async def advance(self, *, seconds: float) -> None:
        delta_ms = int(float(seconds) * 1000)
        async with self._condition:
            self.now_ms_value += delta_ms
            self.monotonic_ms_value += delta_ms
            self._condition.notify_all()
        await asyncio.sleep(0)
        await asyncio.sleep(0)


async def test_debounced_trigger_coalesces_within_window() -> None:
    clock = _ManualClock()
    calls: list[int] = []
    called = asyncio.Event()

    async def callback() -> None:
        calls.append(clock.monotonic_ms())
        called.set()

    debouncer = DebouncedTrigger(callback=callback, clock=clock, debounce_s=10.0)
    debouncer.start()

    try:
        debouncer.trigger()
        await clock.advance(seconds=5.0)
        debouncer.trigger()

        await clock.advance(seconds=9.0)
        assert calls == []

        await clock.advance(seconds=1.0)
        await asyncio.wait_for(called.wait(), timeout=1.0)
        assert calls == [15_000]

        await clock.advance(seconds=100.0)
        assert calls == [15_000]
    finally:
        await debouncer.aclose()


async def test_debounced_trigger_shutdown_prevents_callback() -> None:
    clock = _ManualClock()
    calls = 0

    async def callback() -> None:
        nonlocal calls
        calls += 1

    debouncer = DebouncedTrigger(callback=callback, clock=clock, debounce_s=10.0)
    debouncer.start()
    debouncer.trigger()

    await asyncio.wait_for(debouncer.aclose(), timeout=1.0)
    await clock.advance(seconds=100.0)
    assert calls == 0


async def test_periodic_ticker_fires_on_schedule() -> None:
    clock = _ManualClock()
    ticks: list[int] = []
    tick_queue: asyncio.Queue[int] = asyncio.Queue()

    async def callback() -> None:
        now = clock.monotonic_ms()
        ticks.append(now)
        tick_queue.put_nowait(now)

    ticker = PeriodicTicker(callback=callback, clock=clock, planner_interval_s=5.0)
    ticker.start()

    try:
        await clock.advance(seconds=4.0)
        assert ticks == []

        await clock.advance(seconds=1.0)
        assert await asyncio.wait_for(tick_queue.get(), timeout=1.0) == 5_000
        assert ticks == [5_000]

        await clock.advance(seconds=5.0)
        assert await asyncio.wait_for(tick_queue.get(), timeout=1.0) == 10_000
        assert ticks == [5_000, 10_000]
    finally:
        await ticker.aclose()


async def test_periodic_ticker_shutdown_stops_and_unblocks() -> None:
    clock = _ManualClock()
    ticks = 0

    async def callback() -> None:
        nonlocal ticks
        ticks += 1

    ticker = PeriodicTicker(callback=callback, clock=clock, planner_interval_s=5.0)
    ticker.start()

    await asyncio.wait_for(ticker.aclose(), timeout=1.0)
    await clock.advance(seconds=100.0)
    assert ticks == 0


async def test_debounced_trigger_survives_callback_failures() -> None:
    clock = _ManualClock()
    calls = 0
    succeeded = asyncio.Event()

    async def callback() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("boom")
        succeeded.set()

    debouncer = DebouncedTrigger(callback=callback, clock=clock, debounce_s=5.0)
    debouncer.start()

    try:
        debouncer.trigger()
        await clock.advance(seconds=5.0)
        await asyncio.sleep(0)
        assert calls == 1

        debouncer.trigger()
        await clock.advance(seconds=5.0)
        await asyncio.wait_for(succeeded.wait(), timeout=1.0)
        assert calls == 2
    finally:
        await debouncer.aclose()


async def test_periodic_ticker_survives_callback_failures() -> None:
    clock = _ManualClock()
    ticks = 0
    succeeded = asyncio.Event()

    async def callback() -> None:
        nonlocal ticks
        ticks += 1
        if ticks == 1:
            raise RuntimeError("boom")
        succeeded.set()

    ticker = PeriodicTicker(callback=callback, clock=clock, planner_interval_s=5.0)
    ticker.start()

    try:
        await clock.advance(seconds=5.0)
        await asyncio.sleep(0)
        assert ticks == 1

        await clock.advance(seconds=5.0)
        await asyncio.wait_for(succeeded.wait(), timeout=1.0)
        assert ticks == 2
    finally:
        await ticker.aclose()

"""Planning trigger utilities (debounce + periodic).

This module provides small orchestration helpers for kicking off planning cycles:

- `DebouncedTrigger`: coalesces many `trigger()` calls into a single callback execution after a
  debounce window.
- `PeriodicTicker`: fires a callback every `planner_interval_s`.

Both utilities support clean shutdown and accept an injected `Clock` for deterministic tests.

Clean Architecture:
- Orchestrator is application-layer code.
- This module may depend on `reflexor.domain` and stdlib.
- Forbidden: FastAPI/SQLAlchemy/httpx/worker/API/CLI imports.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Protocol

from reflexor.domain.models_event import Event
from reflexor.orchestrator.clock import Clock

Callback = Callable[[], Awaitable[None]]
logger = logging.getLogger(__name__)


class Trigger(Protocol):
    """A trigger that decides whether it matches an event (placeholder interface)."""

    trigger_id: str

    def matches(self, event: Event) -> bool: ...


async def _sleep_until(*, clock: Clock, deadline_monotonic_ms: int) -> None:
    while True:
        remaining_ms = deadline_monotonic_ms - clock.monotonic_ms()
        if remaining_ms <= 0:
            return
        await clock.sleep(remaining_ms / 1000)


async def _wait_for_event_or_deadline(
    event: asyncio.Event,
    *,
    deadline_monotonic_ms: int,
    clock: Clock,
) -> bool:
    """Return True if `event` fired, False if the deadline elapsed."""

    if clock.monotonic_ms() >= deadline_monotonic_ms:
        return False

    event_task = asyncio.create_task(event.wait())
    sleep_task = asyncio.create_task(
        _sleep_until(clock=clock, deadline_monotonic_ms=deadline_monotonic_ms)
    )
    tasks = {event_task, sleep_task}

    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except asyncio.CancelledError:
        for task in tasks:
            task.cancel()
        with suppress(asyncio.CancelledError):
            await asyncio.gather(*tasks, return_exceptions=True)
        raise
    else:
        for task in pending:
            task.cancel()
        with suppress(asyncio.CancelledError):
            await asyncio.gather(*pending, return_exceptions=True)
        return event_task in done


class DebouncedTrigger:
    """Debounce bursty triggers into a single callback execution."""

    def __init__(
        self,
        *,
        callback: Callback,
        clock: Clock,
        debounce_s: float,
    ) -> None:
        self._callback = callback
        self._clock = clock
        self._debounce_s = float(debounce_s)
        if self._debounce_s <= 0:
            raise ValueError("debounce_s must be > 0")

        self._trigger_event = asyncio.Event()
        self._closed_event = asyncio.Event()
        self._last_trigger_monotonic_ms: int | None = None
        self._task: asyncio.Task[None] | None = None

    def is_closed(self) -> bool:
        return self._closed_event.is_set()

    def start(self) -> None:
        if self._task is not None:
            return
        loop = asyncio.get_running_loop()
        self._task = loop.create_task(self._run_loop())

    def trigger(self) -> None:
        if self._closed_event.is_set():
            return
        self._last_trigger_monotonic_ms = self._clock.monotonic_ms()
        self._trigger_event.set()

    async def aclose(self) -> None:
        if self._closed_event.is_set():
            return
        self._closed_event.set()
        self._trigger_event.set()

        if self._task is None:
            return

        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task

    async def _run_loop(self) -> None:
        try:
            while True:
                await self._trigger_event.wait()
                if self._closed_event.is_set():
                    return
                self._trigger_event.clear()

                last_trigger_ms = (
                    self._clock.monotonic_ms()
                    if self._last_trigger_monotonic_ms is None
                    else self._last_trigger_monotonic_ms
                )
                due_ms = last_trigger_ms + int(self._debounce_s * 1000)
                while True:
                    if self._closed_event.is_set():
                        return

                    remaining_ms = due_ms - self._clock.monotonic_ms()
                    if remaining_ms <= 0:
                        if self._trigger_event.is_set():
                            self._trigger_event.clear()
                            last_trigger_ms = (
                                self._clock.monotonic_ms()
                                if self._last_trigger_monotonic_ms is None
                                else self._last_trigger_monotonic_ms
                            )
                            due_ms = last_trigger_ms + int(self._debounce_s * 1000)
                            continue
                        break

                    triggered = await _wait_for_event_or_deadline(
                        self._trigger_event,
                        deadline_monotonic_ms=due_ms,
                        clock=self._clock,
                    )
                    if self._closed_event.is_set():
                        return
                    if triggered:
                        self._trigger_event.clear()
                        last_trigger_ms = (
                            self._clock.monotonic_ms()
                            if self._last_trigger_monotonic_ms is None
                            else self._last_trigger_monotonic_ms
                        )
                        due_ms = last_trigger_ms + int(self._debounce_s * 1000)

                try:
                    await self._callback()
                except Exception:  # pragma: no cover - exercised via log-only resilience tests
                    logger.exception("debounced trigger callback failed")
        except asyncio.CancelledError:
            return


class PeriodicTicker:
    """Periodic scheduler that fires a callback every `planner_interval_s`."""

    def __init__(
        self,
        *,
        callback: Callback,
        clock: Clock,
        planner_interval_s: float,
    ) -> None:
        self._callback = callback
        self._clock = clock
        self._planner_interval_s = float(planner_interval_s)
        if self._planner_interval_s <= 0:
            raise ValueError("planner_interval_s must be > 0")

        self._closed_event = asyncio.Event()
        self._next_tick_monotonic_ms: int | None = None
        self._task: asyncio.Task[None] | None = None

    def is_closed(self) -> bool:
        return self._closed_event.is_set()

    def start(self) -> None:
        if self._task is not None:
            return
        if self._next_tick_monotonic_ms is None:
            self._next_tick_monotonic_ms = self._clock.monotonic_ms() + int(
                self._planner_interval_s * 1000
            )
        loop = asyncio.get_running_loop()
        self._task = loop.create_task(self._run_loop())

    async def aclose(self) -> None:
        if self._closed_event.is_set():
            return
        self._closed_event.set()

        if self._task is None:
            return

        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task

    async def _run_loop(self) -> None:
        try:
            while True:
                if self._closed_event.is_set():
                    return

                next_tick_ms = self._next_tick_monotonic_ms or (
                    self._clock.monotonic_ms() + int(self._planner_interval_s * 1000)
                )
                remaining_ms = next_tick_ms - self._clock.monotonic_ms()
                if remaining_ms > 0:
                    await self._clock.sleep(remaining_ms / 1000)
                    continue

                if self._closed_event.is_set():
                    return
                try:
                    await self._callback()
                except Exception:  # pragma: no cover - exercised via log-only resilience tests
                    logger.exception("periodic ticker callback failed")
                self._next_tick_monotonic_ms = self._clock.monotonic_ms() + int(
                    self._planner_interval_s * 1000
                )
        except asyncio.CancelledError:
            return


__all__ = [
    "DebouncedTrigger",
    "PeriodicTicker",
    "Trigger",
]

"""Signal handling helpers for worker processes.

The worker is a long-running runtime process. These helpers provide best-effort SIGINT/SIGTERM
handling to initiate a graceful shutdown.
"""

from __future__ import annotations

import asyncio
import signal
from collections.abc import Callable
from dataclasses import dataclass
from signal import Signals
from types import FrameType
from typing import Any


@dataclass(frozen=True, slots=True)
class ShutdownSignal:
    """Represents a received shutdown signal."""

    signal: Signals


SignalHandler = Callable[[int, FrameType | None], Any] | int | signal.Handlers | None


def install_shutdown_handlers(
    stop_event: asyncio.Event,
    *,
    on_signal: Callable[[ShutdownSignal], None] | None = None,
    signals: tuple[Signals, ...] = (signal.SIGINT, signal.SIGTERM),
) -> Callable[[], None]:
    """Install shutdown handlers that set `stop_event` when SIGINT/SIGTERM is received.

    Notes:
    - On UNIX with asyncio, we prefer `loop.add_signal_handler`.
    - If not supported (e.g., some platforms), we fall back to `signal.signal` and marshal the
      callback into the event loop thread-safely.
    - Returns a best-effort cleanup function that restores previous handlers.
    """

    if not isinstance(stop_event, asyncio.Event):
        raise TypeError("stop_event must be an asyncio.Event")

    loop = asyncio.get_running_loop()
    cleanup_actions: list[Callable[[], None]] = []

    def request_shutdown(sig: Signals) -> None:
        try:
            if on_signal is not None:
                on_signal(ShutdownSignal(signal=sig))
        finally:
            stop_event.set()

    for sig in signals:
        previous_handler: SignalHandler = signal.getsignal(sig)
        try:
            loop.add_signal_handler(sig, request_shutdown, sig)
        except (NotImplementedError, RuntimeError):
            # Fallback: synchronous handler (main thread) that schedules the stop in the loop.
            def handler(
                signum: int,
                _frame: FrameType | None,
                *,
                _sig: Signals = sig,
            ) -> None:
                _ = signum
                loop.call_soon_threadsafe(request_shutdown, _sig)

            try:
                signal.signal(sig, handler)
            except ValueError:
                continue

            def cleanup_fallback(
                *,
                _sig: Signals = sig,
                _previous_handler: SignalHandler = previous_handler,
            ) -> None:
                try:
                    signal.signal(_sig, _previous_handler)
                except (ValueError, OSError):
                    return

            cleanup_actions.append(cleanup_fallback)
        else:
            def cleanup_asyncio(
                *,
                _sig: Signals = sig,
                _previous_handler: SignalHandler = previous_handler,
            ) -> None:
                try:
                    loop.remove_signal_handler(_sig)
                except (NotImplementedError, RuntimeError):
                    return

                try:
                    signal.signal(_sig, _previous_handler)
                except (ValueError, OSError):
                    return

            cleanup_actions.append(cleanup_asyncio)

    def cleanup() -> None:
        for action in reversed(cleanup_actions):
            action()

    return cleanup


__all__ = ["ShutdownSignal", "install_shutdown_handlers"]

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


@dataclass(frozen=True, slots=True)
class ShutdownSignal:
    """Represents a received shutdown signal."""

    signal: Signals


def install_shutdown_handlers(
    stop_event: asyncio.Event,
    *,
    on_signal: Callable[[ShutdownSignal], None] | None = None,
    signals: tuple[Signals, ...] = (signal.SIGINT, signal.SIGTERM),
) -> None:
    """Install shutdown handlers that set `stop_event` when SIGINT/SIGTERM is received.

    Notes:
    - On UNIX with asyncio, we prefer `loop.add_signal_handler`.
    - If not supported (e.g., some platforms), we fall back to `signal.signal` and marshal the
      callback into the event loop thread-safely.
    """

    if not isinstance(stop_event, asyncio.Event):
        raise TypeError("stop_event must be an asyncio.Event")

    loop = asyncio.get_running_loop()

    def request_shutdown(sig: Signals) -> None:
        if on_signal is not None:
            on_signal(ShutdownSignal(signal=sig))
        stop_event.set()

    for sig in signals:
        try:
            loop.add_signal_handler(sig, request_shutdown, sig)
        except (NotImplementedError, RuntimeError):
            # Fallback: synchronous handler (main thread) that schedules the stop in the loop.
            def handler(signum: int, _frame: object | None, *, _sig: Signals = sig) -> None:
                _ = signum
                loop.call_soon_threadsafe(request_shutdown, _sig)

            signal.signal(sig, handler)


__all__ = ["ShutdownSignal", "install_shutdown_handlers"]

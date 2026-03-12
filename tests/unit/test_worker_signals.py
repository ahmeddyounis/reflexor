from __future__ import annotations

import asyncio
import signal
from signal import Signals

import pytest

from reflexor.worker.signals import ShutdownSignal, install_shutdown_handlers


class _AsyncioLoop:
    def __init__(self) -> None:
        self.callbacks: dict[Signals, tuple[object, tuple[object, ...]]] = {}
        self.removed: list[Signals] = []

    def add_signal_handler(self, sig: Signals, callback: object, *args: object) -> None:
        self.callbacks[sig] = (callback, args)

    def remove_signal_handler(self, sig: Signals) -> bool:
        self.removed.append(sig)
        self.callbacks.pop(sig, None)
        return True


class _FallbackLoop:
    def __init__(self) -> None:
        self.scheduled: list[tuple[object, tuple[object, ...]]] = []

    def add_signal_handler(self, sig: Signals, callback: object, *args: object) -> None:
        _ = (sig, callback, args)
        raise NotImplementedError

    def call_soon_threadsafe(self, callback: object, *args: object) -> None:
        self.scheduled.append((callback, args))


@pytest.mark.asyncio
async def test_install_shutdown_handlers_asyncio_branch_restores_previous_handlers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop = _AsyncioLoop()
    stop_event = asyncio.Event()
    seen: list[ShutdownSignal] = []
    previous_handlers = {
        signal.SIGINT: signal.SIG_DFL,
        signal.SIGTERM: signal.SIG_IGN,
    }
    restored: list[tuple[Signals, object]] = []

    monkeypatch.setattr(asyncio, "get_running_loop", lambda: loop)
    monkeypatch.setattr(signal, "getsignal", lambda sig: previous_handlers[sig])
    monkeypatch.setattr(signal, "signal", lambda sig, handler: restored.append((sig, handler)))

    cleanup = install_shutdown_handlers(stop_event, on_signal=seen.append)

    callback, args = loop.callbacks[signal.SIGTERM]
    assert callable(callback)
    callback(*args)

    assert stop_event.is_set()
    assert [item.signal for item in seen] == [signal.SIGTERM]

    cleanup()

    assert loop.removed == [signal.SIGTERM, signal.SIGINT]
    assert restored == [
        (signal.SIGTERM, signal.SIG_IGN),
        (signal.SIGINT, signal.SIG_DFL),
    ]


@pytest.mark.asyncio
async def test_install_shutdown_handlers_fallback_sets_stop_event_even_when_callback_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop = _FallbackLoop()
    stop_event = asyncio.Event()
    previous_handlers = {
        signal.SIGINT: signal.SIG_DFL,
        signal.SIGTERM: signal.SIG_IGN,
    }
    installed: dict[Signals, object] = {}
    restored: list[tuple[Signals, object]] = []

    def fake_signal(sig: Signals, handler: object) -> object:
        if sig not in installed:
            installed[sig] = handler
        else:
            restored.append((sig, handler))
        return previous_handlers[sig]

    monkeypatch.setattr(asyncio, "get_running_loop", lambda: loop)
    monkeypatch.setattr(signal, "getsignal", lambda sig: previous_handlers[sig])
    monkeypatch.setattr(signal, "signal", fake_signal)

    def on_signal(_event: ShutdownSignal) -> None:
        raise RuntimeError("boom")

    cleanup = install_shutdown_handlers(stop_event, on_signal=on_signal)

    handler = installed[signal.SIGINT]
    assert callable(handler)
    handler(int(signal.SIGINT), None)

    scheduled_callback, scheduled_args = loop.scheduled[0]
    assert callable(scheduled_callback)
    with pytest.raises(RuntimeError, match="boom"):
        scheduled_callback(*scheduled_args)

    assert stop_event.is_set()

    cleanup()

    assert restored == [
        (signal.SIGTERM, signal.SIG_IGN),
        (signal.SIGINT, signal.SIG_DFL),
    ]

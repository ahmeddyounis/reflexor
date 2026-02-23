from __future__ import annotations

from collections.abc import Callable

from reflexor.config import ReflexorSettings
from reflexor.infra.queue.in_memory_queue import InMemoryQueue
from reflexor.orchestrator.queue import Queue


def build_queue(
    settings: ReflexorSettings,
    *,
    now_ms: Callable[[], int] | None = None,
) -> Queue:
    """Build a `Queue` implementation from settings.

    This is a composition-root helper: it wires configuration into a concrete infrastructure
    backend while returning the narrow `Queue` interface for DI.
    """

    if settings.queue_backend == "inmemory":
        return InMemoryQueue.from_settings(settings, now_ms=now_ms)

    raise ValueError(f"unknown queue backend: {settings.queue_backend!r}")

from __future__ import annotations

import importlib
from uuid import uuid4

import pytest
from pytest import MonkeyPatch

from reflexor.config import ReflexorSettings
from reflexor.infra.queue.factory import build_queue
from reflexor.infra.queue.in_memory_queue import InMemoryQueue
from reflexor.infra.queue.redis_streams import RedisStreamsQueue
from reflexor.orchestrator.queue import TaskEnvelope
from reflexor.orchestrator.queue.observer import NoopQueueObserver


async def test_build_queue_defaults_to_inmemory_and_wires_visibility_timeout() -> None:
    now_ms = 0

    def clock() -> int:
        return now_ms

    settings = ReflexorSettings(queue_visibility_timeout_s=7.5)
    queue = build_queue(settings, now_ms=clock)
    assert isinstance(queue, InMemoryQueue)

    envelope = TaskEnvelope(
        envelope_id=str(uuid4()),
        task_id=str(uuid4()),
        run_id=str(uuid4()),
        attempt=0,
        created_at_ms=0,
        available_at_ms=0,
    )
    await queue.enqueue(envelope)

    lease = await queue.dequeue()
    assert lease is not None
    assert lease.visibility_timeout_s == 7.5


def test_build_queue_returns_redis_streams_queue_when_configured(monkeypatch: MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_from_settings(
        cls: type[RedisStreamsQueue],
        settings: ReflexorSettings,
        *,
        now_ms: object = None,
        observer: object = None,
    ) -> RedisStreamsQueue:
        captured["settings"] = settings
        captured["now_ms"] = now_ms
        captured["observer"] = observer
        return object.__new__(RedisStreamsQueue)

    monkeypatch.setattr(RedisStreamsQueue, "from_settings", classmethod(fake_from_settings))

    def clock() -> int:
        return 0

    observer = NoopQueueObserver()
    settings = ReflexorSettings(queue_backend="redis_streams", redis_url="redis://localhost:6379/0")
    queue = build_queue(settings, now_ms=clock, observer=observer)
    assert isinstance(queue, RedisStreamsQueue)
    assert captured["settings"] is settings
    assert captured["now_ms"] is clock
    assert captured["observer"] is observer


def test_build_queue_reports_missing_redis_dependency(monkeypatch: MonkeyPatch) -> None:
    real_import_module = importlib.import_module

    def fake_import_module(name: str, package: str | None = None) -> object:
        if name == "reflexor.infra.queue.redis_streams":
            raise ModuleNotFoundError("No module named 'redis'", name="redis")
        return real_import_module(name, package)

    monkeypatch.setattr(importlib, "import_module", fake_import_module)

    settings = ReflexorSettings(queue_backend="redis_streams", redis_url="redis://localhost:6379/0")

    with pytest.raises(RuntimeError, match="requires the optional 'redis' dependency"):
        build_queue(settings)

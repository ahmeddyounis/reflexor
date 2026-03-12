from __future__ import annotations

from uuid import uuid4

import pytest

from reflexor.infra.queue.redis_streams.codec import _FIELD_ENVELOPE, _canonical_envelope_json
from reflexor.infra.queue.redis_streams.core import RedisStreamsQueue
from reflexor.orchestrator.queue import TaskEnvelope
from reflexor.orchestrator.queue.observer import (
    QueueAckObservation,
    QueueDequeueObservation,
    QueueEnqueueObservation,
    QueueNackObservation,
    QueueObserver,
    QueueRedeliverObservation,
)


class _FakeRedis:
    def __init__(self) -> None:
        self.stream_entries: list[tuple[str, dict[str, str]]] = []
        self.pending_by_id: dict[str, list[object]] = {}
        self.xack_results: dict[str, int] = {}
        self.eval_responses: list[object] = []
        self.xack_calls: list[tuple[str, str, str]] = []
        self.xdel_calls: list[tuple[str, str]] = []
        self.xadd_calls: list[tuple[str, dict[str, str], int | None, bool]] = []
        self.zadd_calls: list[tuple[str, dict[str, int]]] = []
        self.closed = False

    async def xgroup_create(self, *args: object, **kwargs: object) -> None:
        _ = (args, kwargs)

    async def ping(self) -> bool:
        return True

    async def zadd(self, key: str, mapping: dict[str, int]) -> int:
        self.zadd_calls.append((key, mapping))
        return 1

    async def xadd(
        self,
        stream_key: str,
        fields: dict[str, str],
        *,
        maxlen: int | None = None,
        approximate: bool = True,
    ) -> str:
        self.xadd_calls.append((stream_key, fields, maxlen, approximate))
        return f"{len(self.xadd_calls)}-0"

    async def xautoclaim(self, *args: object, **kwargs: object) -> list[object]:
        _ = (args, kwargs)
        return ["0-0", [], []]

    async def xreadgroup(
        self,
        group: str,
        consumer: str,
        streams: dict[str, str],
        *,
        count: int,
        block: int | None,
    ) -> list[tuple[str, list[tuple[str, dict[str, str]]]]]:
        _ = (group, consumer, count, block)
        stream_key = next(iter(streams))
        if not self.stream_entries:
            return []
        entry = self.stream_entries.pop(0)
        return [(stream_key, [entry])]

    async def xpending_range(
        self,
        stream_key: str,
        group: str,
        *,
        min: str,
        max: str,
        count: int,
    ) -> list[object]:
        _ = (stream_key, group, max, count)
        return self.pending_by_id.get(min, [{"times_delivered": 1}])

    async def xack(self, stream_key: str, group: str, message_id: str) -> int:
        self.xack_calls.append((stream_key, group, message_id))
        return self.xack_results.get(message_id, 1)

    async def xdel(self, stream_key: str, message_id: str) -> int:
        self.xdel_calls.append((stream_key, message_id))
        return 1

    async def eval(self, script: str, numkeys: int, *keys_and_args: str) -> object:
        _ = (script, numkeys, keys_and_args)
        if self.eval_responses:
            return self.eval_responses.pop(0)
        return [1, ""]

    async def xinfo_groups(self, stream_key: str) -> list[dict[str, object]]:
        return [{"name": "group", "pending": 0, "lag": len(self.stream_entries)}]

    async def zcard(self, key: str) -> int:
        _ = key
        return 0

    async def aclose(self, *, close_connection_pool: bool = True) -> None:
        _ = close_connection_pool
        self.closed = True


class _FakeRedisAsyncioModule:
    def __init__(self, client: _FakeRedis) -> None:
        self._client = client

    class Redis:
        _client: _FakeRedis | None = None

        @classmethod
        def from_url(cls, url: str, *, decode_responses: bool = True) -> _FakeRedis:
            _ = (url, decode_responses)
            assert cls._client is not None
            return cls._client


class _FailingObserver:
    def on_enqueue(self, observation: QueueEnqueueObservation) -> None:
        raise RuntimeError("observer boom")

    def on_dequeue(self, observation: QueueDequeueObservation) -> None:
        raise RuntimeError("observer boom")

    def on_ack(self, observation: QueueAckObservation) -> None:
        raise RuntimeError("observer boom")

    def on_nack(self, observation: QueueNackObservation) -> None:
        raise RuntimeError("observer boom")

    def on_redeliver(self, observation: QueueRedeliverObservation) -> None:
        raise RuntimeError("observer boom")


class _RecordingObserver:
    def __init__(self) -> None:
        self.nacks: list[QueueNackObservation] = []

    def on_enqueue(self, observation: QueueEnqueueObservation) -> None:
        _ = observation

    def on_dequeue(self, observation: QueueDequeueObservation) -> None:
        _ = observation

    def on_ack(self, observation: QueueAckObservation) -> None:
        _ = observation

    def on_nack(self, observation: QueueNackObservation) -> None:
        self.nacks.append(observation)

    def on_redeliver(self, observation: QueueRedeliverObservation) -> None:
        _ = observation


def _envelope(*, created_at_ms: int = 0, available_at_ms: int = 0) -> TaskEnvelope:
    return TaskEnvelope(
        envelope_id=str(uuid4()),
        task_id=str(uuid4()),
        run_id=str(uuid4()),
        attempt=0,
        created_at_ms=created_at_ms,
        available_at_ms=available_at_ms,
    )


def _make_queue(
    monkeypatch: pytest.MonkeyPatch,
    *,
    client: _FakeRedis,
    observer: QueueObserver | None = None,
    now_ms: int = 0,
) -> RedisStreamsQueue:
    module = _FakeRedisAsyncioModule(client)
    module.Redis._client = client
    monkeypatch.setattr(
        "reflexor.infra.queue.redis_streams.core._import_redis_asyncio",
        lambda: module,
    )

    def clock() -> int:
        return now_ms

    return RedisStreamsQueue(
        redis_url="redis://localhost:6379/0",
        stream_key="stream",
        consumer_group="group",
        consumer_name="consumer",
        delayed_zset_key="delayed",
        stream_maxlen=None,
        claim_batch_size=10,
        promote_batch_size=10,
        min_claim_idle_ms=1000,
        now_ms=clock,
        observer=observer,
    )


@pytest.mark.asyncio
async def test_redis_streams_queue_observer_failures_do_not_break_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeRedis()
    envelope = _envelope()
    client.stream_entries.append(("1-0", {_FIELD_ENVELOPE: _canonical_envelope_json(envelope)}))
    client.pending_by_id["1-0"] = [{"times_delivered": 1}]

    queue = _make_queue(monkeypatch, client=client, observer=_FailingObserver())

    await queue.enqueue(envelope)
    lease = await queue.dequeue(timeout_s=5, wait_s=0.0)
    assert lease is not None
    assert lease.envelope.envelope_id == envelope.envelope_id

    client.eval_responses.append([1, "2-0"])
    await queue.nack(lease, delay_s=0.0, reason="tests")

    await queue.ack(
        lease.model_copy(update={"lease_id": "2-0"})
    )
    await queue.aclose()
    assert client.closed is True


@pytest.mark.asyncio
async def test_redis_streams_queue_observer_failures_do_not_break_redelivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeRedis()
    envelope = _envelope()
    client.pending_by_id["1-0"] = [{"times_delivered": 2}]
    queue = _make_queue(monkeypatch, client=client, observer=_FailingObserver())

    async def _fake_try_claim_expired(
        _queue: RedisStreamsQueue,
        *,
        claim_idle_ms: int,
    ) -> tuple[str, dict[str, str]] | None:
        _ = claim_idle_ms
        return ("1-0", {_FIELD_ENVELOPE: _canonical_envelope_json(envelope)})

    monkeypatch.setattr(
        "reflexor.infra.queue.redis_streams.core.try_claim_expired",
        _fake_try_claim_expired,
    )

    lease = await queue.dequeue(timeout_s=5, wait_s=0.0)
    assert lease is not None
    assert lease.envelope.envelope_id == envelope.envelope_id
    assert lease.envelope.attempt == 1


@pytest.mark.asyncio
async def test_redis_streams_queue_dequeue_drops_invalid_entry_and_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeRedis()
    valid_envelope = _envelope()
    client.stream_entries.extend(
        [
            ("1-0", {"unexpected": "value"}),
            ("2-0", {_FIELD_ENVELOPE: _canonical_envelope_json(valid_envelope)}),
        ]
    )
    client.pending_by_id["2-0"] = [{"times_delivered": 1}]

    queue = _make_queue(monkeypatch, client=client)

    lease = await queue.dequeue(timeout_s=5, wait_s=0.0)
    assert lease is not None
    assert lease.envelope.envelope_id == valid_envelope.envelope_id
    assert ("stream", "group", "1-0") in client.xack_calls
    assert ("stream", "1-0") in client.xdel_calls


@pytest.mark.asyncio
async def test_redis_streams_queue_nack_skips_observer_when_ack_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeRedis()
    observer = _RecordingObserver()
    envelope = _envelope()
    client.stream_entries.append(("1-0", {_FIELD_ENVELOPE: _canonical_envelope_json(envelope)}))
    client.pending_by_id["1-0"] = [{"times_delivered": 1}]

    queue = _make_queue(monkeypatch, client=client, observer=observer)
    lease = await queue.dequeue(timeout_s=5, wait_s=0.0)
    assert lease is not None

    client.eval_responses.append([0, ""])
    await queue.nack(lease, delay_s=0.0, reason="tests")

    assert observer.nacks == []


@pytest.mark.asyncio
async def test_redis_streams_queue_rejects_non_finite_timing_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeRedis()
    module = _FakeRedisAsyncioModule(client)
    module.Redis._client = client
    monkeypatch.setattr(
        "reflexor.infra.queue.redis_streams.core._import_redis_asyncio",
        lambda: module,
    )

    with pytest.raises(ValueError, match="default_visibility_timeout_s must be finite and > 0"):
        RedisStreamsQueue(
            redis_url="redis://localhost:6379/0",
            stream_key="stream",
            consumer_group="group",
            consumer_name="consumer",
            delayed_zset_key="delayed",
            stream_maxlen=None,
            claim_batch_size=10,
            promote_batch_size=10,
            min_claim_idle_ms=1000,
            default_visibility_timeout_s=float("inf"),
        )

    queue = _make_queue(monkeypatch, client=client)

    with pytest.raises(ValueError, match="timeout_s must be finite and > 0"):
        await queue.dequeue(timeout_s=float("nan"), wait_s=0.0)

    with pytest.raises(ValueError, match="wait_s must be finite and >= 0 when provided"):
        await queue.dequeue(timeout_s=5.0, wait_s=float("inf"))

    envelope = _envelope()
    client.stream_entries.append(("1-0", {_FIELD_ENVELOPE: _canonical_envelope_json(envelope)}))
    client.pending_by_id["1-0"] = [{"times_delivered": 1}]
    lease = await queue.dequeue(timeout_s=5.0, wait_s=0.0)
    assert lease is not None

    with pytest.raises(ValueError, match="delay_s must be finite and >= 0"):
        await queue.nack(lease, delay_s=float("nan"))

    with pytest.raises(ValueError, match="timeout_s must be finite and > 0"):
        await queue.ping(timeout_s=float("inf"))

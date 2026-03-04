from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
import logging
import time
from collections.abc import Callable
from typing import Any

from reflexor.config import ReflexorSettings
from reflexor.orchestrator.queue import Lease, QueueClosed, TaskEnvelope
from reflexor.orchestrator.queue.interface import system_now_ms
from reflexor.orchestrator.queue.observer import (
    NoopQueueObserver,
    QueueAckObservation,
    QueueDequeueObservation,
    QueueEnqueueObservation,
    QueueNackObservation,
    QueueObserver,
    QueueRedeliverObservation,
    build_queue_correlation_ids,
)

_FIELD_ENVELOPE = "envelope"

_PROMOTE_DELAYED_LUA = """
local delayed_key = KEYS[1]
local stream_key = KEYS[2]

local now_ms = tonumber(ARGV[1])
local count = tonumber(ARGV[2])
local field_name = ARGV[3]
local maxlen = ARGV[4]

local due = redis.call('ZRANGEBYSCORE', delayed_key, '-inf', now_ms, 'LIMIT', 0, count)

local moved = 0
for _, payload in ipairs(due) do
  local removed = redis.call('ZREM', delayed_key, payload)
  if removed == 1 then
    if maxlen ~= '' then
      redis.call('XADD', stream_key, 'MAXLEN', '~', tonumber(maxlen), '*', field_name, payload)
    else
      redis.call('XADD', stream_key, '*', field_name, payload)
    end
    moved = moved + 1
  end
end

return moved
"""

_ACK_AND_REQUEUE_LUA = """
local stream_key = KEYS[1]
local delayed_key = KEYS[2]

local group = ARGV[1]
local message_id = ARGV[2]
local payload = ARGV[3]
local available_at_ms = tonumber(ARGV[4])
local enqueue_immediate = ARGV[5]
local field_name = ARGV[6]
local maxlen = ARGV[7]

local acked = redis.call('XACK', stream_key, group, message_id)
if acked == 0 then
  return ''
end

if enqueue_immediate == '1' then
  if maxlen ~= '' then
    return redis.call('XADD', stream_key, 'MAXLEN', '~', tonumber(maxlen), '*', field_name, payload)
  else
    return redis.call('XADD', stream_key, '*', field_name, payload)
  end
end

redis.call('ZADD', delayed_key, available_at_ms, payload)
return ''
"""


def _is_unknown_command_error(exc: Exception, *, command: str) -> bool:
    message = str(exc).lower()
    needle = command.lower()
    return "unknown command" in message and needle in message


def _import_redis_asyncio() -> Any:
    if importlib.util.find_spec("redis") is None:
        raise RuntimeError(
            "Missing optional dependency redis.\n"
            "- If working from the repo: pip install -e '.[redis]'\n"
            "- If installing the package: pip install 'reflexor[redis]'"
        )
    return importlib.import_module("redis.asyncio")


def _canonical_envelope_json(envelope: TaskEnvelope) -> str:
    payload = envelope.model_dump(mode="json")
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _decode_envelope(payload: str) -> TaskEnvelope:
    data = json.loads(payload)
    return TaskEnvelope.model_validate(data)


def _extract_stream_entries(response: Any) -> list[tuple[str, dict[str, str]]]:
    if not response:
        return []

    entries: list[tuple[str, dict[str, str]]] = []
    for _stream_name, stream_entries in response:
        for message_id, fields in stream_entries:
            if not isinstance(message_id, str):
                message_id = str(message_id)

            normalized_fields: dict[str, str] = {}
            for key, value in (fields or {}).items():
                normalized_key = key if isinstance(key, str) else str(key)
                normalized_value = value if isinstance(value, str) else str(value)
                normalized_fields[normalized_key] = normalized_value

            entries.append((message_id, normalized_fields))
    return entries


def _extract_autoclaim_response(response: Any) -> tuple[str, list[tuple[str, dict[str, str]]]]:
    if not response:
        return "0-0", []

    if not isinstance(response, (list, tuple)) or len(response) < 2:
        raise TypeError("unexpected XAUTOCLAIM response shape")

    next_start = response[0]
    messages = response[1]

    next_start_id = next_start if isinstance(next_start, str) else str(next_start)

    normalized: list[tuple[str, dict[str, str]]] = []
    for message_id, fields in messages or []:
        normalized_id = message_id if isinstance(message_id, str) else str(message_id)

        normalized_fields: dict[str, str] = {}
        for key, value in (fields or {}).items():
            normalized_key = key if isinstance(key, str) else str(key)
            normalized_value = value if isinstance(value, str) else str(value)
            normalized_fields[normalized_key] = normalized_value

        normalized.append((normalized_id, normalized_fields))

    return next_start_id, normalized


def _extract_times_delivered(pending_entries: Any, *, default: int = 1) -> int:
    if not pending_entries:
        return int(default)

    entry = pending_entries[0]
    if isinstance(entry, dict):
        raw = entry.get("times_delivered", entry.get("deliveries", entry.get("delivery_count")))
        return int(raw) if raw is not None else int(default)

    times = getattr(entry, "times_delivered", None)
    if times is not None:
        return int(times)

    deliveries = getattr(entry, "deliveries", None)
    if deliveries is not None:
        return int(deliveries)

    if isinstance(entry, (list, tuple)) and len(entry) >= 4:
        return int(entry[3])

    return int(default)


class RedisStreamsQueue:
    """Redis Streams-backed task queue.

    Message encoding:
    - Stream field `{_FIELD_ENVELOPE}` stores a canonical JSON string of the `TaskEnvelope`
      (sorted keys, compact separators).

    Visibility timeout / redelivery:
    - Before blocking on new work, `dequeue()` attempts to reclaim (redeliver) pending
      stream entries idle longer than the configured visibility timeout.
    - Primary mechanism: `XAUTOCLAIM` (Redis 6.2+) with an internal cursor to avoid rescanning from
      `0-0` every time.
    - Fallback: if `XAUTOCLAIM` is not available, we use a bounded `XPENDING` (range) + `XCLAIM`
      scan. This is best-effort and may miss eligible messages when there are many pending entries.

    Attempt semantics (deterministic):
    - The stored envelope JSON contains a base attempt counter (`TaskEnvelope.attempt`).
    - On delivery, `Lease.envelope.attempt` is computed as:
        base_attempt + (redis_times_delivered - 1)
      where `redis_times_delivered` comes from XPENDING for the entry ID.
    - On `nack(...)`, the entry is XACK'd and a *new* message is enqueued with
      `attempt = lease.attempt + 1` and `available_at_ms` set based on `delay_s`.
    """

    def __init__(
        self,
        *,
        redis_url: str,
        stream_key: str,
        consumer_group: str,
        consumer_name: str,
        delayed_zset_key: str,
        stream_maxlen: int | None,
        claim_batch_size: int,
        promote_batch_size: int,
        min_claim_idle_ms: int,
        now_ms: Callable[[], int] | None = None,
        default_visibility_timeout_s: float = 60.0,
        observer: QueueObserver | None = None,
    ) -> None:
        self._logger = logging.getLogger("reflexor.queue.redis_streams")
        self._redis_url = redis_url
        self._stream_key = stream_key
        self._group = consumer_group
        self._consumer = consumer_name
        self._delayed_zset_key = delayed_zset_key
        self._stream_maxlen = stream_maxlen
        self._claim_batch_size = int(claim_batch_size)
        self._promote_batch_size = int(promote_batch_size)
        self._min_claim_idle_ms = int(min_claim_idle_ms)

        if self._claim_batch_size <= 0:
            raise ValueError("claim_batch_size must be > 0")
        if self._promote_batch_size <= 0:
            raise ValueError("promote_batch_size must be > 0")
        if self._min_claim_idle_ms <= 0:
            raise ValueError("min_claim_idle_ms must be > 0")

        self._now_ms = now_ms or system_now_ms
        self._default_visibility_timeout_s = float(default_visibility_timeout_s)
        if self._default_visibility_timeout_s <= 0:
            raise ValueError("default_visibility_timeout_s must be > 0")

        if self._stream_maxlen is not None and int(self._stream_maxlen) <= 0:
            raise ValueError("stream_maxlen must be > 0 when set")

        self._observer = NoopQueueObserver() if observer is None else observer

        redis_asyncio = _import_redis_asyncio()
        self._redis = redis_asyncio.Redis.from_url(
            self._redis_url,
            decode_responses=True,
        )

        self._closed = False
        self._init_lock = asyncio.Lock()
        self._initialized = False
        self._ready_logged = False

        self._autoclaim_lock = asyncio.Lock()
        self._autoclaim_supported: bool | None = None
        self._autoclaim_start_id = "0-0"
        self._claimed_buffer: asyncio.Queue[tuple[str, dict[str, str]]] = asyncio.Queue()

    @classmethod
    def from_settings(
        cls,
        settings: ReflexorSettings,
        *,
        now_ms: Callable[[], int] | None = None,
        observer: QueueObserver | None = None,
    ) -> RedisStreamsQueue:
        if not settings.redis_url:
            raise ValueError(
                "redis_url must be set when queue_backend=redis_streams "
                "(set REFLEXOR_REDIS_URL=redis://...)"
            )

        return cls(
            redis_url=str(settings.redis_url),
            stream_key=str(settings.redis_stream_key),
            consumer_group=str(settings.redis_consumer_group),
            consumer_name=str(settings.redis_consumer_name),
            delayed_zset_key=str(settings.redis_delayed_zset_key),
            stream_maxlen=settings.redis_stream_maxlen,
            claim_batch_size=settings.redis_claim_batch_size,
            promote_batch_size=settings.redis_promote_batch_size,
            min_claim_idle_ms=settings.redis_visibility_timeout_ms,
            now_ms=now_ms,
            default_visibility_timeout_s=settings.queue_visibility_timeout_s,
            observer=observer,
        )

    async def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        async with self._init_lock:
            try:
                await self._redis.xgroup_create(
                    self._stream_key,
                    self._group,
                    id="0-0",
                    mkstream=True,
                )
            except Exception as exc:
                # BUSYGROUP: group already exists
                if "BUSYGROUP" not in str(exc).upper():
                    raise
            self._initialized = True

    async def ensure_ready(self) -> None:
        """Ensure the stream + consumer group exist (idempotent).

        This is intended to be called once at process startup so first-run deployments (docker,
        CI, etc.) don't require manual Redis initialization.
        """

        await self._ensure_initialized()
        if self._ready_logged:
            return
        self._ready_logged = True
        self._logger.info(
            "redis streams queue ready",
            extra={
                "event_type": "queue.redis_streams.ready",
                "stream_key": self._stream_key,
                "consumer_group": self._group,
                "consumer_name": self._consumer,
            },
        )

    async def _promote_delayed(self, *, now_ms: int) -> None:
        maxlen = "" if self._stream_maxlen is None else str(int(self._stream_maxlen))
        await self._redis.eval(
            _PROMOTE_DELAYED_LUA,
            2,
            self._delayed_zset_key,
            self._stream_key,
            str(int(now_ms)),
            str(int(self._promote_batch_size)),
            _FIELD_ENVELOPE,
            maxlen,
        )

    async def _queue_depth(self) -> int:
        try:
            groups = await self._redis.xinfo_groups(self._stream_key)
        except Exception:
            groups = []

        pending = 0
        lag = 0
        for group in groups or []:
            if not isinstance(group, dict):
                continue
            name = group.get("name")
            if name != self._group:
                continue
            try:
                pending = int(group.get("pending") or 0)
            except (TypeError, ValueError):
                pending = 0
            raw_lag = group.get("lag")
            if raw_lag is None:
                lag = 0
            else:
                try:
                    lag = int(raw_lag)
                except (TypeError, ValueError):
                    lag = 0
            break

        delayed = int(await self._redis.zcard(self._delayed_zset_key))
        return int(pending + lag + delayed)

    async def enqueue(self, envelope: TaskEnvelope) -> None:
        if self._closed:
            raise QueueClosed("queue is closed")

        await self._ensure_initialized()

        assert envelope.created_at_ms is not None
        assert envelope.available_at_ms is not None

        now_ms = int(self._now_ms())
        payload = _canonical_envelope_json(envelope)

        if int(envelope.available_at_ms) > now_ms:
            await self._redis.zadd(self._delayed_zset_key, {payload: int(envelope.available_at_ms)})
        else:
            await self._redis.xadd(
                self._stream_key,
                {_FIELD_ENVELOPE: payload},
                maxlen=self._stream_maxlen,
                approximate=True,
            )

        depth = await self._queue_depth()
        self._observer.on_enqueue(
            QueueEnqueueObservation(
                envelope=envelope,
                correlation_ids=build_queue_correlation_ids(envelope),
                now_ms=now_ms,
                queue_depth=depth,
            )
        )

    async def _try_claim_expired(
        self,
        *,
        claim_idle_ms: int,
    ) -> tuple[str, dict[str, str]] | None:
        try:
            return self._claimed_buffer.get_nowait()
        except asyncio.QueueEmpty:
            pass

        async with self._autoclaim_lock:
            try:
                return self._claimed_buffer.get_nowait()
            except asyncio.QueueEmpty:
                pass

            if self._autoclaim_supported is not False:
                try:
                    response = await self._redis.xautoclaim(
                        self._stream_key,
                        self._group,
                        self._consumer,
                        min_idle_time=int(claim_idle_ms),
                        start_id=self._autoclaim_start_id,
                        count=int(self._claim_batch_size),
                        justid=False,
                    )
                except Exception as exc:
                    if _is_unknown_command_error(exc, command="XAUTOCLAIM"):
                        self._autoclaim_supported = False
                    else:
                        raise
                else:
                    self._autoclaim_supported = True
                    next_start, entries = _extract_autoclaim_response(response)
                    self._autoclaim_start_id = next_start
                    for entry in entries:
                        self._claimed_buffer.put_nowait(entry)

            if self._claimed_buffer.empty() and self._autoclaim_supported is False:
                await self._try_claim_expired_fallback(claim_idle_ms=int(claim_idle_ms))

        try:
            return self._claimed_buffer.get_nowait()
        except asyncio.QueueEmpty:
            return None

    async def _try_claim_expired_fallback(self, *, claim_idle_ms: int) -> None:
        pending = await self._redis.execute_command(
            "XPENDING",
            self._stream_key,
            self._group,
            "-",
            "+",
            int(self._claim_batch_size),
        )

        candidate_ids: list[str] = []
        for entry in pending or []:
            if not isinstance(entry, (list, tuple)) or len(entry) < 3:
                continue
            message_id = entry[0]
            idle_ms = entry[2]
            try:
                if int(idle_ms) < int(claim_idle_ms):
                    continue
            except (TypeError, ValueError):
                continue
            candidate_ids.append(message_id if isinstance(message_id, str) else str(message_id))

        if not candidate_ids:
            return

        claimed = await self._redis.execute_command(
            "XCLAIM",
            self._stream_key,
            self._group,
            self._consumer,
            int(claim_idle_ms),
            *candidate_ids,
        )

        for item in claimed or []:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                continue

            raw_id, raw_fields = item
            message_id = raw_id if isinstance(raw_id, str) else str(raw_id)

            fields: dict[str, str] = {}
            if isinstance(raw_fields, dict):
                for key, value in raw_fields.items():
                    fields[key if isinstance(key, str) else str(key)] = (
                        value if isinstance(value, str) else str(value)
                    )
            elif isinstance(raw_fields, (list, tuple)):
                for i in range(0, len(raw_fields), 2):
                    if i + 1 >= len(raw_fields):
                        break
                    key = raw_fields[i]
                    value = raw_fields[i + 1]
                    fields[key if isinstance(key, str) else str(key)] = (
                        value if isinstance(value, str) else str(value)
                    )

            self._claimed_buffer.put_nowait((message_id, fields))

    async def _lease_from_entry(
        self,
        *,
        message_id: str,
        fields: dict[str, str],
        leased_at_ms: int,
        visibility_timeout_s: float,
    ) -> Lease:
        payload = fields.get(_FIELD_ENVELOPE)
        if payload is None:
            raise ValueError("missing envelope field in stream entry")

        envelope = _decode_envelope(payload)

        pending = await self._redis.xpending_range(
            self._stream_key,
            self._group,
            min=message_id,
            max=message_id,
            count=1,
        )
        times_delivered = _extract_times_delivered(pending, default=1)
        computed_attempt = int(envelope.attempt) + max(0, int(times_delivered) - 1)

        leased_envelope = envelope.model_copy(update={"attempt": computed_attempt})
        return Lease(
            lease_id=message_id,
            envelope=leased_envelope,
            leased_at_ms=leased_at_ms,
            visibility_timeout_s=float(visibility_timeout_s),
            attempt=computed_attempt,
        )

    async def dequeue(
        self,
        timeout_s: float | None = None,
        *,
        wait_s: float | None = 0.0,
    ) -> Lease | None:
        if self._closed:
            raise QueueClosed("queue is closed")

        await self._ensure_initialized()

        visibility_timeout_s = (
            self._default_visibility_timeout_s if timeout_s is None else float(timeout_s)
        )
        if visibility_timeout_s <= 0:
            raise ValueError("timeout_s must be > 0")

        if wait_s is not None and float(wait_s) < 0:
            raise ValueError("wait_s must be >= 0")

        claim_idle_ms = max(
            int(visibility_timeout_s * 1000),
            int(self._min_claim_idle_ms),
        )

        started_s = time.monotonic()
        deadline_s = None if wait_s is None else (started_s + float(wait_s))

        block_step_s = 0.25

        while True:
            now_ms = int(self._now_ms())
            await self._promote_delayed(now_ms=now_ms)

            expired = await self._try_claim_expired(claim_idle_ms=claim_idle_ms)
            if expired is not None:
                message_id, fields = expired
                lease = await self._lease_from_entry(
                    message_id=message_id,
                    fields=fields,
                    leased_at_ms=now_ms,
                    visibility_timeout_s=visibility_timeout_s,
                )

                depth = await self._queue_depth()
                self._observer.on_redeliver(
                    QueueRedeliverObservation(
                        envelope=lease.envelope,
                        correlation_ids=build_queue_correlation_ids(lease.envelope),
                        expired_lease_id=lease.lease_id,
                        expired_attempt=max(0, int(lease.attempt) - 1),
                        leased_at_ms=lease.leased_at_ms,
                        deadline_ms=lease.leased_at_ms + int(lease.visibility_timeout_s * 1000),
                        visibility_timeout_s=lease.visibility_timeout_s,
                        now_ms=now_ms,
                        queue_depth=depth,
                    )
                )
                self._observer.on_dequeue(
                    QueueDequeueObservation(
                        lease=lease,
                        correlation_ids=build_queue_correlation_ids(lease.envelope),
                        now_ms=now_ms,
                        queue_depth=depth,
                    )
                )
                return lease

            if wait_s is not None and float(wait_s) == 0.0:
                block_ms = None
            else:
                if deadline_s is None:
                    remaining_s = block_step_s
                else:
                    remaining_s = max(0.0, deadline_s - time.monotonic())
                    remaining_s = min(remaining_s, block_step_s)

                if remaining_s <= 0:
                    depth = await self._queue_depth()
                    self._observer.on_dequeue(
                        QueueDequeueObservation(
                            lease=None, correlation_ids=None, now_ms=now_ms, queue_depth=depth
                        )
                    )
                    return None

                block_ms = int(max(1.0, remaining_s * 1000.0))

            response = await self._redis.xreadgroup(
                self._group,
                self._consumer,
                {self._stream_key: ">"},
                count=1,
                block=block_ms,
            )

            entries = _extract_stream_entries(response)
            if not entries:
                if wait_s is not None and float(wait_s) == 0.0:
                    depth = await self._queue_depth()
                    self._observer.on_dequeue(
                        QueueDequeueObservation(
                            lease=None, correlation_ids=None, now_ms=now_ms, queue_depth=depth
                        )
                    )
                    return None
                continue

            message_id, fields = entries[0]
            lease = await self._lease_from_entry(
                message_id=message_id,
                fields=fields,
                leased_at_ms=now_ms,
                visibility_timeout_s=visibility_timeout_s,
            )

            if (
                lease.envelope.available_at_ms is not None
                and lease.envelope.available_at_ms > now_ms
            ):
                await self._redis.eval(
                    _ACK_AND_REQUEUE_LUA,
                    2,
                    self._stream_key,
                    self._delayed_zset_key,
                    self._group,
                    lease.lease_id,
                    _canonical_envelope_json(lease.envelope),
                    str(int(lease.envelope.available_at_ms)),
                    "0",
                    _FIELD_ENVELOPE,
                    "" if self._stream_maxlen is None else str(int(self._stream_maxlen)),
                )
                continue

            depth = await self._queue_depth()
            self._observer.on_dequeue(
                QueueDequeueObservation(
                    lease=lease,
                    correlation_ids=build_queue_correlation_ids(lease.envelope),
                    now_ms=now_ms,
                    queue_depth=depth,
                )
            )
            return lease

    async def ack(self, lease: Lease) -> None:
        if self._closed:
            raise QueueClosed("queue is closed")

        await self._ensure_initialized()

        acked = int(await self._redis.xack(self._stream_key, self._group, lease.lease_id))
        if acked <= 0:
            return

        now_ms = int(self._now_ms())
        depth = await self._queue_depth()
        self._observer.on_ack(
            QueueAckObservation(
                lease=lease,
                correlation_ids=build_queue_correlation_ids(lease.envelope),
                now_ms=now_ms,
                queue_depth=depth,
            )
        )

    async def nack(
        self,
        lease: Lease,
        delay_s: float | None = None,
        reason: str | None = None,
    ) -> None:
        if self._closed:
            raise QueueClosed("queue is closed")

        await self._ensure_initialized()

        delay = 0.0 if delay_s is None else float(delay_s)
        if delay < 0:
            raise ValueError("delay_s must be >= 0")

        now_ms = int(self._now_ms())
        available_at_ms = now_ms + int(delay * 1000)
        requeued_attempt = int(lease.envelope.attempt) + 1

        requeued = lease.envelope.model_copy(
            update={"attempt": requeued_attempt, "available_at_ms": available_at_ms}
        )
        payload = _canonical_envelope_json(requeued)

        enqueue_immediate = "1" if delay <= 0 else "0"
        maxlen = "" if self._stream_maxlen is None else str(int(self._stream_maxlen))

        await self._redis.eval(
            _ACK_AND_REQUEUE_LUA,
            2,
            self._stream_key,
            self._delayed_zset_key,
            self._group,
            lease.lease_id,
            payload,
            str(int(available_at_ms)),
            enqueue_immediate,
            _FIELD_ENVELOPE,
            maxlen,
        )

        depth = await self._queue_depth()
        self._observer.on_nack(
            QueueNackObservation(
                lease=lease,
                correlation_ids=build_queue_correlation_ids(lease.envelope),
                delay_s=float(delay),
                reason=reason,
                now_ms=now_ms,
                queue_depth=depth,
            )
        )

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._redis.aclose(close_connection_pool=True)


__all__ = ["RedisStreamsQueue"]

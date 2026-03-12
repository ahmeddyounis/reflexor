from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Callable

from reflexor.config import ReflexorSettings
from reflexor.infra.queue.redis_streams.claiming import try_claim_expired
from reflexor.infra.queue.redis_streams.codec import (
    _FIELD_ENVELOPE,
    _canonical_envelope_json,
)
from reflexor.infra.queue.redis_streams.delayed import promote_delayed
from reflexor.infra.queue.redis_streams.depth import queue_depth
from reflexor.infra.queue.redis_streams.leasing import InvalidStreamEntryError, lease_from_entry
from reflexor.infra.queue.redis_streams.lua import _ACK_AND_REQUEUE_LUA
from reflexor.infra.queue.redis_streams.redis_helpers import (
    _extract_ack_and_requeue_result,
    _extract_stream_entries,
    _import_redis_asyncio,
)
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
    notify_queue_observer,
)


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
        if (
            not math.isfinite(self._default_visibility_timeout_s)
            or self._default_visibility_timeout_s <= 0
        ):
            raise ValueError("default_visibility_timeout_s must be finite and > 0")

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

    async def ping(self, *, timeout_s: float = 0.2) -> bool:
        """Return True if Redis is reachable.

        This is intended for fast health checks and does not create streams/groups.
        """

        if self._closed:
            return False

        timeout = float(timeout_s)
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout_s must be finite and > 0")

        try:
            await asyncio.wait_for(self._redis.ping(), timeout=timeout)
        except Exception:
            return False
        return True

    async def _drop_invalid_entry(self, *, message_id: str, error: InvalidStreamEntryError) -> None:
        self._logger.warning(
            "dropping invalid redis stream entry",
            extra={
                "event_type": "queue.redis_streams.invalid_entry",
                "stream_key": self._stream_key,
                "consumer_group": self._group,
                "consumer_name": self._consumer,
                "message_id": message_id,
                "error": str(error),
            },
        )
        await self._redis.xack(self._stream_key, self._group, message_id)
        await self._redis.xdel(self._stream_key, message_id)

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

        depth = await queue_depth(self)
        notify_queue_observer(
            self._observer,
            callback_name="on_enqueue",
            observation=QueueEnqueueObservation(
                envelope=envelope,
                correlation_ids=build_queue_correlation_ids(envelope),
                now_ms=now_ms,
                queue_depth=depth,
            ),
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
        if not math.isfinite(visibility_timeout_s) or visibility_timeout_s <= 0:
            raise ValueError("timeout_s must be finite and > 0")

        wait_seconds = None if wait_s is None else float(wait_s)
        if wait_seconds is not None and (not math.isfinite(wait_seconds) or wait_seconds < 0):
            raise ValueError("wait_s must be finite and >= 0 when provided")

        claim_idle_ms = max(
            int(visibility_timeout_s * 1000),
            int(self._min_claim_idle_ms),
        )

        started_s = time.monotonic()
        deadline_s = None if wait_seconds is None else (started_s + wait_seconds)

        block_step_s = 0.25

        while True:
            now_ms = int(self._now_ms())
            await promote_delayed(self, now_ms=now_ms)

            expired = await try_claim_expired(self, claim_idle_ms=claim_idle_ms)
            if expired is not None:
                message_id, fields = expired
                try:
                    lease = await lease_from_entry(
                        self,
                        message_id=message_id,
                        fields=fields,
                        leased_at_ms=now_ms,
                        visibility_timeout_s=visibility_timeout_s,
                    )
                except InvalidStreamEntryError as exc:
                    await self._drop_invalid_entry(message_id=message_id, error=exc)
                    continue

                depth = await queue_depth(self)
                notify_queue_observer(
                    self._observer,
                    callback_name="on_redeliver",
                    observation=QueueRedeliverObservation(
                        envelope=lease.envelope,
                        correlation_ids=build_queue_correlation_ids(lease.envelope),
                        expired_lease_id=lease.lease_id,
                        expired_attempt=max(0, int(lease.attempt) - 1),
                        leased_at_ms=lease.leased_at_ms,
                        deadline_ms=lease.leased_at_ms + int(lease.visibility_timeout_s * 1000),
                        visibility_timeout_s=lease.visibility_timeout_s,
                        now_ms=now_ms,
                        queue_depth=depth,
                    ),
                )
                notify_queue_observer(
                    self._observer,
                    callback_name="on_dequeue",
                    observation=QueueDequeueObservation(
                        lease=lease,
                        correlation_ids=build_queue_correlation_ids(lease.envelope),
                        now_ms=now_ms,
                        queue_depth=depth,
                    ),
                )
                return lease

            if wait_seconds is not None and wait_seconds == 0.0:
                block_ms = None
            else:
                if deadline_s is None:
                    remaining_s = block_step_s
                else:
                    remaining_s = max(0.0, deadline_s - time.monotonic())
                    remaining_s = min(remaining_s, block_step_s)

                if remaining_s <= 0:
                    depth = await queue_depth(self)
                    notify_queue_observer(
                        self._observer,
                        callback_name="on_dequeue",
                        observation=QueueDequeueObservation(
                            lease=None, correlation_ids=None, now_ms=now_ms, queue_depth=depth
                        ),
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
                if wait_seconds is not None and wait_seconds == 0.0:
                    depth = await queue_depth(self)
                    notify_queue_observer(
                        self._observer,
                        callback_name="on_dequeue",
                        observation=QueueDequeueObservation(
                            lease=None, correlation_ids=None, now_ms=now_ms, queue_depth=depth
                        ),
                    )
                    return None
                continue

            message_id, fields = entries[0]
            try:
                lease = await lease_from_entry(
                    self,
                    message_id=message_id,
                    fields=fields,
                    leased_at_ms=now_ms,
                    visibility_timeout_s=visibility_timeout_s,
                )
            except InvalidStreamEntryError as exc:
                await self._drop_invalid_entry(message_id=message_id, error=exc)
                continue

            if (
                lease.envelope.available_at_ms is not None
                and lease.envelope.available_at_ms > now_ms
            ):
                response = await self._redis.eval(
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
                acked, _new_message_id = _extract_ack_and_requeue_result(response)
                if not acked:
                    continue
                continue

            depth = await queue_depth(self)
            notify_queue_observer(
                self._observer,
                callback_name="on_dequeue",
                observation=QueueDequeueObservation(
                    lease=lease,
                    correlation_ids=build_queue_correlation_ids(lease.envelope),
                    now_ms=now_ms,
                    queue_depth=depth,
                ),
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
        depth = await queue_depth(self)
        notify_queue_observer(
            self._observer,
            callback_name="on_ack",
            observation=QueueAckObservation(
                lease=lease,
                correlation_ids=build_queue_correlation_ids(lease.envelope),
                now_ms=now_ms,
                queue_depth=depth,
            ),
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
        if not math.isfinite(delay) or delay < 0:
            raise ValueError("delay_s must be finite and >= 0")

        now_ms = int(self._now_ms())
        available_at_ms = now_ms + int(delay * 1000)
        requeued_attempt = int(lease.envelope.attempt) + 1

        requeued = lease.envelope.model_copy(
            update={"attempt": requeued_attempt, "available_at_ms": available_at_ms}
        )
        payload = _canonical_envelope_json(requeued)

        enqueue_immediate = "1" if delay <= 0 else "0"
        maxlen = "" if self._stream_maxlen is None else str(int(self._stream_maxlen))

        response = await self._redis.eval(
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
        acked, _new_message_id = _extract_ack_and_requeue_result(response)
        if not acked:
            return

        depth = await queue_depth(self)
        notify_queue_observer(
            self._observer,
            callback_name="on_nack",
            observation=QueueNackObservation(
                lease=lease,
                correlation_ids=build_queue_correlation_ids(lease.envelope),
                delay_s=float(delay),
                reason=reason,
                now_ms=now_ms,
                queue_depth=depth,
            ),
        )

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._redis.aclose(close_connection_pool=True)


__all__ = ["RedisStreamsQueue"]

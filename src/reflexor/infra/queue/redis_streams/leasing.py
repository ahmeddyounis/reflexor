from __future__ import annotations

from json import JSONDecodeError
from typing import TYPE_CHECKING

from pydantic import ValidationError

from reflexor.infra.queue.redis_streams.codec import _FIELD_ENVELOPE, _decode_envelope
from reflexor.infra.queue.redis_streams.redis_helpers import _extract_times_delivered
from reflexor.orchestrator.queue import Lease

if TYPE_CHECKING:
    from reflexor.infra.queue.redis_streams.core import RedisStreamsQueue


class InvalidStreamEntryError(ValueError):
    pass


async def lease_from_entry(
    queue: RedisStreamsQueue,
    *,
    message_id: str,
    fields: dict[str, str],
    leased_at_ms: int,
    visibility_timeout_s: float,
) -> Lease:
    payload = fields.get(_FIELD_ENVELOPE)
    if payload is None:
        raise InvalidStreamEntryError("missing envelope field in stream entry")
    try:
        envelope = _decode_envelope(payload)
    except (JSONDecodeError, TypeError, ValidationError) as exc:
        raise InvalidStreamEntryError("invalid envelope payload in stream entry") from exc

    pending = await queue._redis.xpending_range(
        queue._stream_key,
        queue._group,
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

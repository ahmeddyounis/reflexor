from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4


def _system_now_ms() -> int:
    return int(time.time() * 1000)


def _require_non_empty_str(value: str, *, field_name: str) -> str:
    trimmed = value.strip()
    if not trimmed:
        raise ValueError(f"{field_name} must be non-empty")
    return trimmed


def _require_json_serializable(value: object, *, field_name: str) -> None:
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be JSON-serializable") from exc


@dataclass(frozen=True, slots=True)
class QueueMessage:
    """A JSON-serializable message envelope.

    This type intentionally stays small and backend-agnostic. Backends may attach additional
    metadata (e.g., delivery attempts) by updating fields on subsequent deliveries.
    """

    message_id: str
    queue_name: str
    payload: dict[str, object]
    enqueued_at_ms: int
    available_at_ms: int
    attempts: int = 0
    dedupe_key: str | None = None

    @classmethod
    def new(
        cls,
        *,
        queue_name: str,
        payload: dict[str, object],
        available_at_ms: int | None = None,
        dedupe_key: str | None = None,
        now_ms: Callable[[], int] = _system_now_ms,
    ) -> QueueMessage:
        normalized_queue = _require_non_empty_str(queue_name, field_name="queue_name")
        _require_json_serializable(payload, field_name="payload")
        now = now_ms()
        normalized_dedupe_key: str | None
        if dedupe_key is None:
            normalized_dedupe_key = None
        elif isinstance(dedupe_key, str):
            trimmed = dedupe_key.strip()
            normalized_dedupe_key = trimmed or None
        else:
            raise TypeError("dedupe_key must be a string or None")
        return cls(
            message_id=str(uuid4()),
            queue_name=normalized_queue,
            payload=dict(payload),
            enqueued_at_ms=now,
            available_at_ms=now if available_at_ms is None else int(available_at_ms),
            attempts=0,
            dedupe_key=normalized_dedupe_key,
        )


class QueueBackend(Protocol):
    """Queue backend interface (ports).

    Orchestrator/application code depends on this Protocol. Concrete backends live in
    `reflexor.infra.queue.*` and must be swappable.
    """

    async def enqueue(
        self,
        *,
        queue_name: str,
        payload: dict[str, object],
        dedupe_key: str | None = None,
        available_at_ms: int | None = None,
    ) -> QueueMessage: ...

    async def reserve(self, *, queue_name: str, lease_ms: int = 60_000) -> QueueMessage | None: ...

    async def ack(self, *, queue_name: str, message_id: str) -> None: ...

    async def nack(self, *, queue_name: str, message_id: str, delay_ms: int = 0) -> None: ...

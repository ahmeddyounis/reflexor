"""Event suppression (runaway loop / cascade protection).

This module implements a small DB-backed counter with a cooldown TTL to suppress repeated events
with the same signature. It is intended to run at the ingestion/orchestrator boundary, before any
tasks are routed/enqueued.

Clean Architecture:
- Orchestrator is application-layer code.
- This module may depend on `reflexor.domain`, `reflexor.observability`, and `reflexor.storage`
  ports/UoW.
- Forbidden: FastAPI/SQLAlchemy/httpx/worker/API/CLI imports.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol, cast

from reflexor.domain.models_event import Event
from reflexor.domain.serialization import canonical_json, stable_sha256
from reflexor.observability.redaction import Redactor
from reflexor.orchestrator.clock import Clock, SystemClock
from reflexor.storage.ports import EventSuppressionRecord, EventSuppressionRepo
from reflexor.storage.uow import DatabaseSession, UnitOfWork


@dataclass(frozen=True, slots=True)
class EventSuppressionOutcome:
    suppressed: bool
    record: EventSuppressionRecord


class EventSuppressor(Protocol):
    async def observe(self, event: Event) -> EventSuppressionOutcome: ...


def _extract_payload_fields(
    payload: Mapping[str, object],
    *,
    field_paths: Sequence[str],
) -> dict[str, object]:
    extracted: dict[str, object] = {}
    for raw_path in field_paths:
        path = raw_path.strip()
        if not path:
            continue

        current: object = payload
        missing = False
        for part in path.split("."):
            if not isinstance(current, Mapping):
                missing = True
                break
            if part not in current:
                missing = True
                break
            current = current[part]

        if not missing:
            extracted[path] = current

    return extracted


@dataclass(slots=True)
class DbEventSuppressor:
    """DB-backed event suppressor.

    Behavior:
    - Counts events per signature in a fixed window (`window_s`).
    - When count exceeds `threshold`, the signature enters a suppressed state until `ttl_s` passes.
    - Suppression is persisted to DB to survive restarts.
    - After suppression expires, the counter window resets.
    """

    uow_factory: Callable[[], UnitOfWork]
    repo: Callable[[DatabaseSession], EventSuppressionRepo]
    clock: Clock = SystemClock()
    signature_fields: Sequence[str] = field(default_factory=tuple)
    window_s: float = 60.0
    threshold: int = 50
    ttl_s: float = 300.0
    redactor: Redactor = field(default_factory=Redactor)
    max_signature_bytes: int = 4096

    def __post_init__(self) -> None:
        if float(self.window_s) <= 0:
            raise ValueError("window_s must be > 0")
        if int(self.threshold) <= 0:
            raise ValueError("threshold must be > 0")
        if float(self.ttl_s) <= 0:
            raise ValueError("ttl_s must be > 0")
        if int(self.max_signature_bytes) <= 0:
            raise ValueError("max_signature_bytes must be > 0")

    async def observe(self, event: Event) -> EventSuppressionOutcome:
        now_ms = int(self.clock.now_ms())
        window_ms = int(float(self.window_s) * 1000)
        ttl_ms = int(float(self.ttl_s) * 1000)

        signature_obj: dict[str, object] = {
            "type": event.type.strip(),
            "source": event.source.strip(),
        }

        if self.signature_fields:
            extracted = _extract_payload_fields(event.payload, field_paths=self.signature_fields)
            signature_obj["fields"] = extracted
        elif event.dedupe_key is not None:
            signature_obj["dedupe_key"] = event.dedupe_key

        redacted = self.redactor.redact(signature_obj, max_bytes=int(self.max_signature_bytes))
        if not isinstance(redacted, dict):  # pragma: no cover
            raise ValueError("redacted signature must be a JSON object")

        signature_json = cast(dict[str, object], redacted)
        signature_hash = stable_sha256(canonical_json(signature_json))

        uow = self.uow_factory()
        async with uow:
            repo = self.repo(uow.session)

            existing = await repo.get(signature_hash)
            if existing is not None and now_ms >= int(existing.expires_at_ms):
                await repo.delete(signature_hash)
                existing = None

            if existing is not None and existing.suppressed_until_ms is not None:
                existing_suppressed_until_ms = int(existing.suppressed_until_ms)
                if now_ms < existing_suppressed_until_ms:
                    updated_count = int(existing.count) + 1
                    updated_record = EventSuppressionRecord(
                        signature_hash=signature_hash,
                        event_type=event.type.strip(),
                        event_source=event.source.strip(),
                        signature=signature_json,
                        window_start_ms=int(existing.window_start_ms),
                        count=updated_count,
                        threshold=int(existing.threshold),
                        window_ms=int(existing.window_ms),
                        suppressed_until_ms=existing_suppressed_until_ms,
                        resume_required=bool(existing.resume_required),
                        cleared_at_ms=existing.cleared_at_ms,
                        cleared_by=existing.cleared_by,
                        cleared_request_id=existing.cleared_request_id,
                        created_at_ms=int(existing.created_at_ms),
                        updated_at_ms=now_ms,
                        expires_at_ms=int(existing.expires_at_ms),
                    )
                    stored = await repo.upsert(updated_record)
                    return EventSuppressionOutcome(suppressed=True, record=stored)

                await repo.delete(signature_hash)
                existing = None

            created_at_ms = now_ms
            if existing is None:
                window_start_ms = now_ms
                count = 1
            else:
                existing_window_start = int(existing.window_start_ms)
                if now_ms - existing_window_start >= window_ms:
                    window_start_ms = now_ms
                    count = 1
                else:
                    window_start_ms = existing_window_start
                    count = int(existing.count) + 1
                    created_at_ms = int(existing.created_at_ms)

            suppressed_until_ms: int | None = None
            expires_at_ms = window_start_ms + window_ms
            if count > int(self.threshold):
                suppressed_until_ms = now_ms + ttl_ms
                expires_at_ms = suppressed_until_ms

            record = EventSuppressionRecord(
                signature_hash=signature_hash,
                event_type=event.type.strip(),
                event_source=event.source.strip(),
                signature=signature_json,
                window_start_ms=window_start_ms,
                count=count,
                threshold=int(self.threshold),
                window_ms=window_ms,
                suppressed_until_ms=suppressed_until_ms,
                resume_required=False,
                cleared_at_ms=None if existing is None else existing.cleared_at_ms,
                cleared_by=None if existing is None else existing.cleared_by,
                cleared_request_id=None if existing is None else existing.cleared_request_id,
                created_at_ms=created_at_ms,
                updated_at_ms=now_ms,
                expires_at_ms=int(expires_at_ms),
            )
            stored = await repo.upsert(record)
            return EventSuppressionOutcome(
                suppressed=bool(suppressed_until_ms is not None),
                record=stored,
            )


__all__ = ["DbEventSuppressor", "EventSuppressor", "EventSuppressionOutcome"]

"""Run-packet export helpers.

This module supports exporting a persisted RunPacket to a sanitized JSON file so it can be
shared safely for debugging/replay.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from reflexor.config import ReflexorSettings, get_settings
from reflexor.infra.db.engine import (
    async_session_scope,
    create_async_engine,
    create_async_session_factory,
)
from reflexor.infra.db.repos import SqlAlchemyRunPacketRepo
from reflexor.observability.audit_sanitize import sanitize_for_audit
from reflexor.observability.redaction import Redactor
from reflexor.observability.truncation import TRUNCATION_MARKER, truncate_collection

EXPORT_SCHEMA_VERSION = 1


def _write_export(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")


def _json_dumps(obj: object) -> str:
    return json.dumps(
        obj,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )


def _bounded_export_object(obj: dict[str, object], *, max_bytes: int) -> dict[str, object]:
    if max_bytes <= 0:
        raise ValueError("max_bytes must be > 0")

    redactor = Redactor()
    budget = int(max_bytes)

    for _attempt in range(5):
        candidate = redactor.redact(obj, max_bytes=budget)
        if not isinstance(candidate, dict):
            candidate = {"schema_version": obj.get("schema_version"), TRUNCATION_MARKER: ""}

        serialized = _json_dumps(candidate).encode("utf-8")
        if len(serialized) <= max_bytes:
            return candidate

        # Reduce budget based on the observed size; apply a safety margin to converge quickly.
        budget = max(1, int(budget * (max_bytes / max(1, len(serialized))) * 0.9))

    # Final defense-in-depth: truncate the (already redacted) object structure.
    truncated = truncate_collection(
        redactor.redact(obj),
        max_bytes=max_bytes,
        max_depth=redactor.max_depth,
        max_items=redactor.max_items,
    )
    if isinstance(truncated, dict):
        return truncated
    return {"schema_version": obj.get("schema_version"), TRUNCATION_MARKER: ""}


async def export_run_packet(
    run_id: str,
    out_path: str | Path,
    *,
    include_tasks: bool = True,
    settings: ReflexorSettings | None = None,
) -> Path:
    """Export a sanitized run packet to a JSON file.

    The exported file is safe to share (redacted and truncated), and bounded in size based on
    `ReflexorSettings.max_run_packet_bytes`.
    """

    normalized_run_id = run_id.strip()
    if not normalized_run_id:
        raise ValueError("run_id must be non-empty")

    path = Path(out_path)
    resolved_settings = get_settings() if settings is None else settings

    engine = create_async_engine(resolved_settings)
    session_factory = create_async_session_factory(engine)
    try:
        async with async_session_scope(session_factory) as session:
            repo = SqlAlchemyRunPacketRepo(session, settings=resolved_settings)
            packet = await repo.get(normalized_run_id)
            if packet is None:
                raise KeyError(f"unknown run_id: {normalized_run_id!r}")

            packet_dict: dict[str, Any] = packet.model_dump(mode="json")
            if not include_tasks:
                packet_dict.pop("tasks", None)

            sanitized_packet = sanitize_for_audit(packet_dict, settings=resolved_settings)
    finally:
        await engine.dispose()

    export_obj: dict[str, object] = {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "exported_at_ms": int(time.time() * 1000),
        "packet": sanitized_packet,
    }

    bounded = _bounded_export_object(
        export_obj,
        max_bytes=int(resolved_settings.max_run_packet_bytes),
    )
    payload = _json_dumps(bounded)

    await asyncio.to_thread(_write_export, path, payload)
    return path


__all__ = ["EXPORT_SCHEMA_VERSION", "export_run_packet"]

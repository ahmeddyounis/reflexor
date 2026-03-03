"""Run-packet import helpers.

This module supports importing a previously exported RunPacket JSON file and persisting it
as a *new* run packet record. The imported record is meant for inspection/replay workflows
only; it must not trigger tool execution.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from reflexor.config import ReflexorSettings, get_settings
from reflexor.domain.models_run_packet import RunPacket
from reflexor.infra.db.engine import (
    async_session_scope,
    create_async_engine,
    create_async_session_factory,
)
from reflexor.infra.db.repos import SqlAlchemyRunPacketRepo, SqlAlchemyRunRepo
from reflexor.replay.exporter import EXPORT_SCHEMA_VERSION
from reflexor.storage.ports import RunRecord


class RunPacketImportError(ValueError):
    """Raised when an exported run packet file cannot be imported."""


def _read_export_file(path: Path, *, max_bytes: int) -> bytes:
    if max_bytes <= 0:
        raise ValueError("max_bytes must be > 0")

    if not path.exists():
        raise FileNotFoundError(str(path))
    if not path.is_file():
        raise RunPacketImportError(f"not a file: {path}")

    size = path.stat().st_size
    if size > max_bytes:
        raise RunPacketImportError(f"export file is too large ({size} bytes); max is {max_bytes}")

    data = path.read_bytes()
    if len(data) > max_bytes:
        raise RunPacketImportError(
            f"export file is too large ({len(data)} bytes); max is {max_bytes}"
        )
    return data


def _rewrite_task_run_ids(tasks_obj: object, *, run_id: str) -> object:
    if not isinstance(tasks_obj, list):
        return tasks_obj

    rewritten: list[object] = []
    for item in tasks_obj:
        if isinstance(item, dict):
            updated = dict(item)
            updated["run_id"] = run_id
            rewritten.append(updated)
        else:
            rewritten.append(item)
    return rewritten


async def import_run_packet(
    path: str | Path,
    *,
    parent_run_id: str | None = None,
    settings: ReflexorSettings | None = None,
) -> str:
    """Import an exported run packet JSON file into the database as a replay artifact.

    Returns the new `run_id` created for the imported packet.
    """

    resolved_settings = get_settings() if settings is None else settings
    max_bytes = int(resolved_settings.max_run_packet_bytes)

    file_path = Path(path)
    raw = await asyncio.to_thread(_read_export_file, file_path, max_bytes=max_bytes)

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RunPacketImportError(f"invalid JSON: {exc.msg}") from exc

    if not isinstance(payload, dict):
        raise RunPacketImportError("export JSON must be an object")

    schema_version = payload.get("schema_version")
    if schema_version != EXPORT_SCHEMA_VERSION:
        raise RunPacketImportError(
            f"unsupported schema_version: {schema_version!r}; expected {EXPORT_SCHEMA_VERSION}"
        )

    packet_obj = payload.get("packet")
    if not isinstance(packet_obj, dict):
        raise RunPacketImportError("export JSON must contain a 'packet' object")

    normalized_parent: str | None = None
    if parent_run_id is not None:
        normalized_parent = parent_run_id.strip() or None
        if normalized_parent is None:
            raise RunPacketImportError("parent_run_id must be non-empty when provided")

    new_run_id = str(uuid.uuid4())

    packet_dict: dict[str, Any] = dict(packet_obj)
    original_run_id_obj = packet_dict.get("run_id")
    original_run_id = original_run_id_obj if isinstance(original_run_id_obj, str) else None
    if normalized_parent is None and isinstance(original_run_id, str) and original_run_id.strip():
        normalized_parent = original_run_id.strip()

    packet_dict["run_id"] = new_run_id
    packet_dict["parent_run_id"] = normalized_parent
    packet_dict["tasks"] = _rewrite_task_run_ids(packet_dict.get("tasks"), run_id=new_run_id)

    try:
        packet = RunPacket.model_validate(packet_dict)
    except ValidationError as exc:
        raise RunPacketImportError("exported packet is not a valid RunPacket") from exc

    run_record = RunRecord(
        run_id=packet.run_id,
        parent_run_id=packet.parent_run_id,
        created_at_ms=packet.created_at_ms,
        started_at_ms=packet.started_at_ms,
        completed_at_ms=packet.completed_at_ms,
    )

    engine = create_async_engine(
        resolved_settings.database_url,
        echo=bool(resolved_settings.db_echo),
    )
    session_factory = create_async_session_factory(engine)
    try:
        async with async_session_scope(session_factory) as session:
            async with session.begin():
                await SqlAlchemyRunRepo(session).create(run_record)
                await SqlAlchemyRunPacketRepo(session, settings=resolved_settings).create(packet)
    finally:
        await engine.dispose()

    return new_run_id


__all__ = ["RunPacketImportError", "import_run_packet"]

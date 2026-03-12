"""Run-packet import helpers.

This module supports importing a previously exported RunPacket JSON file and persisting it
as a *new* run packet record. The imported record is meant for inspection/replay workflows
only; it must not trigger tool execution.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Mapping
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
from reflexor.infra.db.repos import SqlAlchemyRunPacketRepo, SqlAlchemyRunRepo, SqlAlchemyTaskRepo
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


def _metadata_with_import_provenance(
    metadata_obj: object,
    *,
    original_run_id: str | None,
    original_task_id: str | None,
    original_tool_call_id: str | None,
) -> dict[str, object]:
    metadata = dict(metadata_obj) if isinstance(metadata_obj, Mapping) else {}
    import_meta_obj = metadata.get("import")
    import_meta = dict(import_meta_obj) if isinstance(import_meta_obj, Mapping) else {}
    import_meta.update(
        {
            "original_run_id": original_run_id,
            "original_task_id": original_task_id,
            "original_tool_call_id": original_tool_call_id,
        }
    )
    metadata["import"] = import_meta
    return metadata


def _rewrite_tool_result_ids(
    tool_results_obj: object,
    *,
    tool_call_id_map: Mapping[str, str],
) -> object:
    if not isinstance(tool_results_obj, list):
        return tool_results_obj

    rewritten: list[object] = []
    for item in tool_results_obj:
        if not isinstance(item, dict):
            rewritten.append(item)
            continue

        updated = dict(item)
        tool_call_id = updated.get("tool_call_id")
        if isinstance(tool_call_id, str):
            updated["tool_call_id"] = tool_call_id_map.get(tool_call_id, tool_call_id)
        rewritten.append(updated)
    return rewritten


def _rewrite_packet_for_import(
    packet_obj: dict[str, Any],
    *,
    new_run_id: str,
    parent_run_id: str | None,
) -> dict[str, Any]:
    packet_dict: dict[str, Any] = dict(packet_obj)
    original_run_id_obj = packet_dict.get("run_id")
    original_run_id = original_run_id_obj if isinstance(original_run_id_obj, str) else None

    task_id_map: dict[str, str] = {}
    tool_call_id_map: dict[str, str] = {}
    rewritten_tasks: list[object] = []

    tasks_obj = packet_dict.get("tasks")
    if isinstance(tasks_obj, list):
        for item in tasks_obj:
            if not isinstance(item, dict):
                rewritten_tasks.append(item)
                continue

            updated = dict(item)
            original_task_id_obj = updated.get("task_id")
            original_task_id = (
                original_task_id_obj if isinstance(original_task_id_obj, str) else None
            )
            if original_task_id is not None:
                task_id_map[original_task_id] = str(uuid.uuid4())
                updated["task_id"] = task_id_map[original_task_id]

            updated["run_id"] = new_run_id

            original_tool_call_id: str | None = None
            tool_call_obj = updated.get("tool_call")
            if isinstance(tool_call_obj, dict):
                rewritten_tool_call = dict(tool_call_obj)
                original_tool_call_id_obj = rewritten_tool_call.get("tool_call_id")
                original_tool_call_id = (
                    original_tool_call_id_obj
                    if isinstance(original_tool_call_id_obj, str)
                    else None
                )
                if original_tool_call_id is not None:
                    tool_call_id_map[original_tool_call_id] = str(uuid.uuid4())
                    rewritten_tool_call["tool_call_id"] = tool_call_id_map[original_tool_call_id]
                updated["tool_call"] = rewritten_tool_call

            updated["metadata"] = _metadata_with_import_provenance(
                updated.get("metadata"),
                original_run_id=original_run_id,
                original_task_id=original_task_id,
                original_tool_call_id=original_tool_call_id,
            )
            rewritten_tasks.append(updated)

        for index, item in enumerate(rewritten_tasks):
            if not isinstance(item, dict):
                continue
            depends_on_obj = item.get("depends_on")
            if not isinstance(depends_on_obj, list):
                continue
            updated = dict(item)
            updated["depends_on"] = [
                task_id_map.get(dep, dep) if isinstance(dep, str) else dep for dep in depends_on_obj
            ]
            rewritten_tasks[index] = updated

    packet_dict["run_id"] = new_run_id
    packet_dict["parent_run_id"] = parent_run_id
    packet_dict["tasks"] = rewritten_tasks
    packet_dict["tool_results"] = _rewrite_tool_result_ids(
        packet_dict.get("tool_results"),
        tool_call_id_map=tool_call_id_map,
    )
    return packet_dict


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

    original_run_id_obj = packet_obj.get("run_id")
    original_run_id = original_run_id_obj if isinstance(original_run_id_obj, str) else None
    if normalized_parent is None and isinstance(original_run_id, str) and original_run_id.strip():
        normalized_parent = original_run_id.strip()

    packet_dict = _rewrite_packet_for_import(
        packet_obj,
        new_run_id=new_run_id,
        parent_run_id=normalized_parent,
    )

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

    engine = create_async_engine(resolved_settings)
    session_factory = create_async_session_factory(engine)
    try:
        async with async_session_scope(session_factory) as session:
            async with session.begin():
                await SqlAlchemyRunRepo(session).create(run_record)
                task_repo = SqlAlchemyTaskRepo(session)
                for task in packet.tasks:
                    await task_repo.create(task)
                await SqlAlchemyRunPacketRepo(session, settings=resolved_settings).create(packet)
    finally:
        await engine.dispose()

    return new_run_id


__all__ = ["RunPacketImportError", "import_run_packet"]

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from reflexor.domain.models_run_packet import RunPacket
from reflexor.replay.exporter import EXPORT_SCHEMA_VERSION
from reflexor.replay.runner.types import ReplayError


def _read_bytes_limited(path: Path, *, max_bytes: int) -> bytes:
    with path.open("rb") as handle:
        data = handle.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ReplayError(f"replay file is too large (>{max_bytes} bytes)")
    return data


def _read_json_file(path: Path, *, max_bytes: int) -> object:
    if max_bytes <= 0:
        raise ValueError("max_bytes must be > 0")

    if not path.exists():
        raise FileNotFoundError(str(path))
    if not path.is_file():
        raise ReplayError(f"not a file: {path}")

    data = _read_bytes_limited(path, max_bytes=max_bytes)

    try:
        return json.loads(data)
    except json.JSONDecodeError as exc:
        raise ReplayError(f"invalid JSON: {exc.msg}") from exc


def _extract_packet(payload: object) -> RunPacket:
    if not isinstance(payload, dict):
        raise ReplayError("export JSON must be an object")

    schema_version = payload.get("schema_version")
    if schema_version != EXPORT_SCHEMA_VERSION:
        raise ReplayError(
            f"unsupported schema_version: {schema_version!r}; expected {EXPORT_SCHEMA_VERSION}"
        )

    packet_obj = payload.get("packet")
    if not isinstance(packet_obj, dict):
        raise ReplayError("export JSON must contain a 'packet' object")

    try:
        return RunPacket.model_validate(packet_obj)
    except ValidationError as exc:
        raise ReplayError("exported packet is not a valid RunPacket") from exc

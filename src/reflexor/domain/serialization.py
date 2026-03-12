from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(obj: Any) -> str:
    """Return a deterministic JSON encoding for JSON-serializable data.

    - Sorted keys
    - No insignificant whitespace (stable separators)
    - UTF-8 friendly (ensure_ascii=False)
    - Standard JSON only (`NaN`/`Infinity` are rejected)
    """

    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def stable_sha256(*parts: str | bytes) -> str:
    """Return a deterministic SHA-256 hex digest for a sequence of parts.

    Parts are length-delimited to avoid ambiguity (e.g., ["ab","c"] != ["a","bc"]).
    """

    hasher = hashlib.sha256()
    for part in parts:
        data = part.encode("utf-8") if isinstance(part, str) else part
        hasher.update(len(data).to_bytes(8, "big", signed=False))
        hasher.update(data)
    return hasher.hexdigest()


def make_idempotency_key(tool_name: str, args: dict[str, object], event_id: str) -> str:
    """Create a stable idempotency key for a tool call."""

    normalized_tool_name = tool_name.strip()
    if not normalized_tool_name:
        raise ValueError("tool_name must be non-empty")

    normalized_event_id = event_id.strip()
    if not normalized_event_id:
        raise ValueError("event_id must be non-empty")

    return stable_sha256(normalized_tool_name, canonical_json(args), normalized_event_id)

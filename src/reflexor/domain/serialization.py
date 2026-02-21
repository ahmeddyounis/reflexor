from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(obj: Any) -> str:
    """Return a deterministic JSON encoding for JSON-serializable data.

    - Sorted keys
    - No insignificant whitespace (stable separators)
    - UTF-8 friendly (ensure_ascii=False)
    """

    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


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

    return stable_sha256(tool_name.strip(), canonical_json(args), event_id.strip())

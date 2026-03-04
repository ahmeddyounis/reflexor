from __future__ import annotations

from uuid import UUID


def _coerce_uuid4_str(value: str | None) -> str | None:
    if value is None:
        return None
    trimmed = value.strip()
    if not trimmed:
        return None
    try:
        parsed = UUID(trimmed)
    except ValueError:
        return None
    if parsed.version != 4:
        return None
    return str(parsed)

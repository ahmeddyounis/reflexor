from __future__ import annotations


def _validate_limit_offset(*, limit: int, offset: int) -> tuple[int, int]:
    limit_int = int(limit)
    offset_int = int(offset)
    if limit_int < 0:
        raise ValueError("limit must be >= 0")
    if offset_int < 0:
        raise ValueError("offset must be >= 0")
    return limit_int, offset_int


def _normalize_optional_str(value: str | None) -> str | None:
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None

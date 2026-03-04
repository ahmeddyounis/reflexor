from __future__ import annotations

import importlib
import importlib.util
from typing import Any


def _is_unknown_command_error(exc: Exception, *, command: str) -> bool:
    message = str(exc).lower()
    needle = command.lower()
    return "unknown command" in message and needle in message


def _import_redis_asyncio() -> Any:
    if importlib.util.find_spec("redis") is None:
        raise RuntimeError(
            "Missing optional dependency redis.\n"
            "- If working from the repo: pip install -e '.[redis]'\n"
            "- If installing the package: pip install 'reflexor[redis]'"
        )
    return importlib.import_module("redis.asyncio")


def _extract_stream_entries(response: Any) -> list[tuple[str, dict[str, str]]]:
    if not response:
        return []

    entries: list[tuple[str, dict[str, str]]] = []
    for _stream_name, stream_entries in response:
        for message_id, fields in stream_entries:
            if not isinstance(message_id, str):
                message_id = str(message_id)

            normalized_fields: dict[str, str] = {}
            for key, value in (fields or {}).items():
                normalized_key = key if isinstance(key, str) else str(key)
                normalized_value = value if isinstance(value, str) else str(value)
                normalized_fields[normalized_key] = normalized_value

            entries.append((message_id, normalized_fields))
    return entries


def _extract_autoclaim_response(response: Any) -> tuple[str, list[tuple[str, dict[str, str]]]]:
    if not response:
        return "0-0", []

    if not isinstance(response, (list, tuple)) or len(response) < 2:
        raise TypeError("unexpected XAUTOCLAIM response shape")

    next_start = response[0]
    messages = response[1]

    next_start_id = next_start if isinstance(next_start, str) else str(next_start)

    normalized: list[tuple[str, dict[str, str]]] = []
    for message_id, fields in messages or []:
        normalized_id = message_id if isinstance(message_id, str) else str(message_id)

        normalized_fields: dict[str, str] = {}
        for key, value in (fields or {}).items():
            normalized_key = key if isinstance(key, str) else str(key)
            normalized_value = value if isinstance(value, str) else str(value)
            normalized_fields[normalized_key] = normalized_value

        normalized.append((normalized_id, normalized_fields))

    return next_start_id, normalized


def _extract_times_delivered(pending_entries: Any, *, default: int = 1) -> int:
    if not pending_entries:
        return int(default)

    entry = pending_entries[0]
    if isinstance(entry, dict):
        raw = entry.get("times_delivered", entry.get("deliveries", entry.get("delivery_count")))
        return int(raw) if raw is not None else int(default)

    times = getattr(entry, "times_delivered", None)
    if times is not None:
        return int(times)

    deliveries = getattr(entry, "deliveries", None)
    if deliveries is not None:
        return int(deliveries)

    if isinstance(entry, (list, tuple)) and len(entry) >= 4:
        return int(entry[3])

    return int(default)

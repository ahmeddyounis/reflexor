from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

TRUNCATION_MARKER = "<truncated>"
TRUNCATION_MARKER_BYTES = TRUNCATION_MARKER.encode("utf-8")
NONFINITE_FLOAT_REPLACEMENT = "<non-finite-float>"


def truncate_str(value: str, *, max_bytes: int, marker: str = TRUNCATION_MARKER) -> str:
    """Truncate a string to `max_bytes` (UTF-8), appending `marker` when truncation occurs."""

    if max_bytes < 0:
        raise ValueError("max_bytes must be >= 0")

    data = value.encode("utf-8")
    if len(data) <= max_bytes:
        return value

    marker_bytes = marker.encode("utf-8")
    if max_bytes <= len(marker_bytes):
        return marker_bytes[:max_bytes].decode("utf-8", errors="ignore")

    keep = max_bytes - len(marker_bytes)
    prefix = data[:keep].decode("utf-8", errors="ignore")
    return f"{prefix}{marker}"


def truncate_bytes(
    value: bytes, *, max_bytes: int, marker: bytes = TRUNCATION_MARKER_BYTES
) -> bytes:
    """Truncate bytes to `max_bytes`, appending `marker` when truncation occurs."""

    if max_bytes < 0:
        raise ValueError("max_bytes must be >= 0")

    if len(value) <= max_bytes:
        return value

    if max_bytes <= len(marker):
        return marker[:max_bytes]

    keep = max_bytes - len(marker)
    return value[:keep] + marker


def estimate_size_bytes(obj: object, *, max_depth: int = 8, max_items: int = 200) -> int:
    """Best-effort estimate of the size (in bytes) of a JSON-like object."""

    return _estimate_size_bytes(obj, depth=0, max_depth=max_depth, max_items=max_items, stack=set())


def truncate_collection(
    obj: object,
    *,
    max_bytes: int,
    marker: str = TRUNCATION_MARKER,
    max_depth: int = 8,
    max_items: int = 200,
) -> object:
    """Truncate a nested structure so its estimated size fits within `max_bytes`.

    This function never raises for unknown input types; it stringifies them safely.
    """

    if max_bytes < 0:
        raise ValueError("max_bytes must be >= 0")

    truncated, _remaining, _did_truncate = _truncate_with_budget(
        obj,
        budget=max_bytes,
        depth=0,
        max_depth=max_depth,
        max_items=max_items,
        marker=marker,
        stack=set(),
    )
    return truncated


def _safe_stringify(obj: object) -> str:
    try:
        return str(obj)
    except Exception:
        try:
            return repr(obj)
        except Exception:
            return f"<unstringifiable {type(obj).__name__}>"


def _estimate_size_bytes(
    obj: object,
    *,
    depth: int,
    max_depth: int,
    max_items: int,
    stack: set[int],
) -> int:
    if obj is None:
        return 0

    if isinstance(obj, (bool, int)):
        return len(repr(obj).encode("utf-8"))

    if isinstance(obj, float):
        if math.isfinite(obj):
            return len(repr(obj).encode("utf-8"))
        return len(NONFINITE_FLOAT_REPLACEMENT.encode("utf-8"))

    if isinstance(obj, str):
        return len(obj.encode("utf-8"))

    if isinstance(obj, (bytes, bytearray, memoryview)):
        return len(bytes(obj))

    if depth >= max_depth:
        return len(TRUNCATION_MARKER_BYTES)

    obj_id = id(obj)
    if obj_id in stack:
        return len(TRUNCATION_MARKER_BYTES)

    if isinstance(obj, Mapping):
        stack.add(obj_id)
        try:
            total = 0
            for idx, (key, value) in enumerate(obj.items()):
                if idx >= max_items:
                    total += len(TRUNCATION_MARKER_BYTES)
                    break
                total += _estimate_size_bytes(
                    key, depth=depth + 1, max_depth=max_depth, max_items=max_items, stack=stack
                )
                total += _estimate_size_bytes(
                    value, depth=depth + 1, max_depth=max_depth, max_items=max_items, stack=stack
                )
            return total
        finally:
            stack.remove(obj_id)

    if isinstance(obj, Sequence) and not isinstance(obj, (str, bytes, bytearray)):
        stack.add(obj_id)
        try:
            total = 0
            for idx, item in enumerate(obj):
                if idx >= max_items:
                    total += len(TRUNCATION_MARKER_BYTES)
                    break
                total += _estimate_size_bytes(
                    item, depth=depth + 1, max_depth=max_depth, max_items=max_items, stack=stack
                )
            return total
        finally:
            stack.remove(obj_id)

    return len(_safe_stringify(obj).encode("utf-8"))


def _truncate_with_budget(
    obj: object,
    *,
    budget: int,
    depth: int,
    max_depth: int,
    max_items: int,
    marker: str,
    stack: set[int],
) -> tuple[object, int, bool]:
    if budget <= 0:
        if isinstance(obj, (bytes, bytearray, memoryview)):
            return (b"", 0, True)
        if isinstance(obj, Mapping):
            return ({}, 0, True)
        if isinstance(obj, Sequence) and not isinstance(obj, (str, bytes, bytearray)):
            return (_restore_sequence_type(obj, []), 0, True)
        return ("", 0, True)

    if obj is None or isinstance(obj, (bool, int)):
        size = estimate_size_bytes(obj, max_depth=0, max_items=0)
        if size <= budget:
            return (obj, budget - size, False)
        return (truncate_str(marker, max_bytes=budget, marker=marker), 0, True)

    if isinstance(obj, float):
        if math.isfinite(obj):
            size = estimate_size_bytes(obj, max_depth=0, max_items=0)
            if size <= budget:
                return (obj, budget - size, False)
            return (truncate_str(marker, max_bytes=budget, marker=marker), 0, True)
        replacement = truncate_str(
            NONFINITE_FLOAT_REPLACEMENT,
            max_bytes=budget,
            marker=marker,
        )
        remaining = budget - len(replacement.encode("utf-8"))
        return (replacement, max(0, remaining), True)

    if isinstance(obj, str):
        size = len(obj.encode("utf-8"))
        if size <= budget:
            return (obj, budget - size, False)
        truncated = truncate_str(obj, max_bytes=budget, marker=marker)
        remaining = budget - len(truncated.encode("utf-8"))
        return (truncated, max(0, remaining), True)

    if isinstance(obj, bytes):
        size = len(obj)
        if size <= budget:
            return (obj, budget - size, False)
        truncated_bytes = truncate_bytes(obj, max_bytes=budget, marker=marker.encode("utf-8"))
        return (truncated_bytes, budget - len(truncated_bytes), True)

    if isinstance(obj, (bytearray, memoryview)):
        return _truncate_with_budget(
            bytes(obj),
            budget=budget,
            depth=depth,
            max_depth=max_depth,
            max_items=max_items,
            marker=marker,
            stack=stack,
        )

    if depth >= max_depth:
        truncated = truncate_str(marker, max_bytes=budget, marker=marker)
        remaining = budget - len(truncated.encode("utf-8"))
        return (truncated, max(0, remaining), True)

    obj_id = id(obj)
    if obj_id in stack:
        truncated = truncate_str(marker, max_bytes=budget, marker=marker)
        remaining = budget - len(truncated.encode("utf-8"))
        return (truncated, max(0, remaining), True)

    if isinstance(obj, Mapping):
        stack.add(obj_id)
        try:
            result: dict[object, object] = {}
            remaining = budget

            total_items = len(obj)
            marker_value = marker
            marker_cost = len(marker_value.encode("utf-8"))

            for idx, (key, value) in enumerate(obj.items()):
                if idx >= max_items:
                    if remaining > 0:
                        marker_key = truncate_str(
                            marker_value, max_bytes=remaining, marker=marker_value
                        )
                        if marker_key:
                            result[marker_key] = ""
                    return (result, 0, True)

                reserve = marker_cost if idx < total_items - 1 else 0
                item_budget = remaining - reserve
                if item_budget <= 0:
                    break

                out_key: object = (
                    key
                    if isinstance(key, (str, int, float, bool, type(None)))
                    else _safe_stringify(key)
                )
                key_size = estimate_size_bytes(out_key, max_depth=0, max_items=0)
                if key_size > item_budget:
                    break

                value_budget = item_budget - key_size
                out_value, _value_remaining, _value_truncated = _truncate_with_budget(
                    value,
                    budget=value_budget,
                    depth=depth + 1,
                    max_depth=max_depth,
                    max_items=max_items,
                    marker=marker,
                    stack=stack,
                )
                value_size = estimate_size_bytes(
                    out_value, max_depth=max_depth, max_items=max_items
                )
                item_size = key_size + value_size
                if item_size > item_budget:
                    break

                result[out_key] = out_value
                remaining -= item_size

            if len(result) < total_items and remaining > 0:
                marker_key = truncate_str(marker_value, max_bytes=remaining, marker=marker_value)
                if marker_key:
                    result[marker_key] = ""
                return (result, 0, True)

            return (result, remaining, False)
        finally:
            stack.remove(obj_id)

    if isinstance(obj, Sequence) and not isinstance(obj, (str, bytes, bytearray)):
        stack.add(obj_id)
        try:
            items: list[object] = []
            remaining = budget

            total_items = len(obj)
            marker_value = marker
            marker_cost = len(marker_value.encode("utf-8"))

            for idx, item in enumerate(obj):
                if idx >= max_items:
                    if remaining > 0:
                        items.append(
                            truncate_str(marker_value, max_bytes=remaining, marker=marker_value)
                        )
                    return (_restore_sequence_type(obj, items), 0, True)

                reserve = marker_cost if idx < total_items - 1 else 0
                item_budget = remaining - reserve
                if item_budget <= 0:
                    break

                out_item, _item_remaining, _item_truncated = _truncate_with_budget(
                    item,
                    budget=item_budget,
                    depth=depth + 1,
                    max_depth=max_depth,
                    max_items=max_items,
                    marker=marker,
                    stack=stack,
                )
                item_size = estimate_size_bytes(out_item, max_depth=max_depth, max_items=max_items)
                if item_size > item_budget:
                    break

                items.append(out_item)
                remaining -= item_size

            if len(items) < total_items and remaining > 0:
                items.append(truncate_str(marker_value, max_bytes=remaining, marker=marker_value))
                return (_restore_sequence_type(obj, items), 0, True)

            return (_restore_sequence_type(obj, items), remaining, False)
        finally:
            stack.remove(obj_id)

    text = _safe_stringify(obj)
    truncated = truncate_str(text, max_bytes=budget, marker=marker)
    remaining = budget - len(truncated.encode("utf-8"))
    return (truncated, max(0, remaining), truncated != text)


def _restore_sequence_type(original: Sequence[object], items: list[object]) -> object:
    if isinstance(original, tuple):
        return tuple(items)
    return items

from __future__ import annotations

from collections.abc import Mapping, Sequence

from reflexor.observability.redaction import Redactor
from reflexor.observability.truncation import (
    TRUNCATION_MARKER,
    TRUNCATION_MARKER_BYTES,
    estimate_size_bytes,
    truncate_bytes,
    truncate_collection,
    truncate_str,
)


def _contains_marker(obj: object) -> bool:
    if obj == TRUNCATION_MARKER:
        return True
    if isinstance(obj, str) and TRUNCATION_MARKER in obj:
        return True
    if isinstance(obj, bytes) and TRUNCATION_MARKER_BYTES in obj:
        return True
    if isinstance(obj, Mapping):
        return any(_contains_marker(k) or _contains_marker(v) for k, v in obj.items())
    if isinstance(obj, Sequence) and not isinstance(obj, (str, bytes, bytearray)):
        return any(_contains_marker(item) for item in obj)
    return False


def test_truncate_str_appends_marker_and_is_deterministic() -> None:
    value = "x" * 100
    truncated = truncate_str(value, max_bytes=30)
    assert truncated.endswith(TRUNCATION_MARKER)
    assert len(truncated.encode("utf-8")) <= 30
    assert truncate_str(value, max_bytes=30) == truncated

    assert truncate_str("hello", max_bytes=30) == "hello"


def test_truncate_bytes_appends_marker_and_is_deterministic() -> None:
    value = b"x" * 100
    truncated = truncate_bytes(value, max_bytes=20)
    assert truncated.endswith(TRUNCATION_MARKER_BYTES)
    assert len(truncated) <= 20
    assert truncate_bytes(value, max_bytes=20) == truncated

    assert truncate_bytes(b"hello", max_bytes=20) == b"hello"


def test_estimate_size_bytes_is_stable_for_key_order() -> None:
    d1 = {"a": "1", "b": "2"}
    d2 = {"b": "2", "a": "1"}
    assert estimate_size_bytes(d1) == estimate_size_bytes(d2)


def test_truncate_collection_caps_nested_structures_and_inserts_marker() -> None:
    obj = {
        "a": "x" * 100,
        "b": {"c": "y" * 100},
        "d": ["z" * 100, "w" * 100],
    }

    truncated = truncate_collection(obj, max_bytes=80)
    assert _contains_marker(truncated)
    assert estimate_size_bytes(truncated) <= 80
    assert truncate_collection(obj, max_bytes=80) == truncated


def test_redactor_truncates_after_redaction_to_avoid_partial_leaks() -> None:
    redactor = Redactor()
    secret = "ghp_" + ("a" * 40)
    text = f"{secret} " + ("x" * 100)

    sanitized = redactor.redact(text, max_bytes=30)
    assert isinstance(sanitized, str)
    assert "ghp_" not in sanitized
    assert TRUNCATION_MARKER in sanitized

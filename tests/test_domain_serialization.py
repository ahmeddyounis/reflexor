from __future__ import annotations

import math

import pytest

from reflexor.domain.serialization import canonical_json, make_idempotency_key, stable_sha256


def test_canonical_json_is_stable_for_dict_key_order() -> None:
    a = {"b": 1, "a": 2, "nested": {"y": 1, "x": 2}}
    b = {"nested": {"x": 2, "y": 1}, "a": 2, "b": 1}
    assert canonical_json(a) == canonical_json(b)
    assert canonical_json(a) == '{"a":2,"b":1,"nested":{"x":2,"y":1}}'


def test_stable_sha256_is_deterministic_and_segmented() -> None:
    assert stable_sha256("a", "b") == stable_sha256("a", "b")
    assert stable_sha256("ab", "c") != stable_sha256("a", "bc")


def test_make_idempotency_key_is_stable_and_sensitive_to_inputs() -> None:
    tool_name = " filesystem.read "
    event_id = "evt_123"

    args1 = {"path": "/tmp/file.txt", "mode": "r"}
    args2 = {"mode": "r", "path": "/tmp/file.txt"}

    k1 = make_idempotency_key(tool_name, args1, event_id)
    k2 = make_idempotency_key(tool_name, args2, event_id)
    assert k1 == k2

    assert k1 != make_idempotency_key(tool_name, args1, "evt_124")
    assert k1 != make_idempotency_key("filesystem.write", args1, event_id)


def test_make_idempotency_key_rejects_non_json_args() -> None:
    with pytest.raises(TypeError):
        make_idempotency_key("x", {"bad": object()}, "evt_123")

    with pytest.raises(ValueError):
        make_idempotency_key("x", {"bad": math.nan}, "evt_123")


def test_make_idempotency_key_rejects_blank_identifiers() -> None:
    with pytest.raises(ValueError, match="tool_name must be non-empty"):
        make_idempotency_key("   ", {"ok": True}, "evt_123")

    with pytest.raises(ValueError, match="event_id must be non-empty"):
        make_idempotency_key("tool", {"ok": True}, "   ")

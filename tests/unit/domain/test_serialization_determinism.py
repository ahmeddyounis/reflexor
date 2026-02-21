from __future__ import annotations

from reflexor.domain.serialization import canonical_json, make_idempotency_key


def test_idempotency_key_is_deterministic_across_dict_order() -> None:
    args1 = {"path": "/tmp/file.txt", "mode": "r"}
    args2 = {"mode": "r", "path": "/tmp/file.txt"}

    assert canonical_json(args1) == canonical_json(args2)
    assert make_idempotency_key("tool", args1, "evt1") == make_idempotency_key(
        "tool", args2, "evt1"
    )

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import cast

from reflexor.observability.redaction import Redactor


def test_redact_nested_dicts_and_lists_by_key() -> None:
    redactor = Redactor()
    payload = {
        "user": {"name": "alice", "password": "p@ssw0rd"},
        "items": [{"token": "t-123"}, {"ok": True}],
    }

    redacted = redactor.redact(payload)

    assert isinstance(redacted, Mapping)
    user = redacted["user"]
    assert isinstance(user, Mapping)
    indexed_user = cast(Mapping[str, object], user)
    items = redacted["items"]
    assert isinstance(items, Sequence)
    indexed_items = cast(Sequence[object], items)
    first_item = indexed_items[0]
    assert isinstance(first_item, Mapping)
    indexed_first_item = cast(Mapping[str, object], first_item)
    payload_user = cast(Mapping[str, object], payload["user"])
    assert payload_user["password"] == "p@ssw0rd"
    assert indexed_user["password"] == "<redacted>"
    assert indexed_first_item["token"] == "<redacted>"


def test_redact_header_like_structures() -> None:
    redactor = Redactor()

    headers = {
        "Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.aaaa.bbbb",
        "Cookie": "sessionid=abc",
        "User-Agent": "pytest",
    }
    redacted_headers = redactor.redact(headers)
    assert isinstance(redacted_headers, Mapping)
    assert redacted_headers["Authorization"] == "<redacted>"
    assert redacted_headers["Cookie"] == "<redacted>"
    assert redacted_headers["User-Agent"] == "pytest"

    header_pairs = [
        ("Authorization", "Bearer abcdefghijklmnop"),
        ("Accept", "application/json"),
    ]
    redacted_pairs = redactor.redact(header_pairs)
    assert isinstance(redacted_pairs, Sequence)
    indexed_pairs = cast(Sequence[object], redacted_pairs)
    first_pair = indexed_pairs[0]
    second_pair = indexed_pairs[1]
    assert isinstance(first_pair, tuple)
    assert isinstance(second_pair, tuple)
    assert first_pair[1] == "<redacted>"
    assert second_pair[1] == "application/json"


def test_redact_regex_matches_in_strings_and_bytes() -> None:
    redactor = Redactor()

    text = "calling https://example.com?token=ghp_abcdefghijklmnopqrstuvwxyz12345"
    redacted_text = redactor.redact(text)
    assert isinstance(redacted_text, str)
    assert "ghp_" not in redacted_text
    assert "<redacted>" in redacted_text

    data = b"Authorization: Bearer abcdefghijklmnop"
    redacted_bytes = redactor.redact(data)
    assert isinstance(redacted_bytes, bytes)
    assert b"abcdef" not in redacted_bytes
    assert b"<redacted>" in redacted_bytes


def test_redact_alias_keys_url_credentials_and_non_finite_floats() -> None:
    redactor = Redactor()

    payload = {
        "X-API-Key": "plain-api-key-value",
        "endpoint_url": "postgresql://operator:super-secret@example.com/reflexor",
        "score": math.nan,
    }

    redacted = redactor.redact(payload)

    assert isinstance(redacted, Mapping)
    assert redacted["X-API-Key"] == "<redacted>"
    assert redacted["endpoint_url"] == "postgresql://<redacted>@example.com/reflexor"
    assert redacted["score"] == "<non-finite-float>"


def test_redact_never_throws_on_unknown_types() -> None:
    class Exploding:
        def __str__(self) -> str:  # pragma: no cover
            raise RuntimeError("boom")

        def __repr__(self) -> str:  # pragma: no cover
            raise RuntimeError("boom")

    redactor = Redactor()
    result = redactor.redact(Exploding())
    assert isinstance(result, str)

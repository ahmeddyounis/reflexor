from __future__ import annotations

from reflexor.observability.redaction import Redactor


def test_redact_nested_dicts_and_lists_by_key() -> None:
    redactor = Redactor()
    payload = {
        "user": {"name": "alice", "password": "p@ssw0rd"},
        "items": [{"token": "t-123"}, {"ok": True}],
    }

    redacted = redactor.redact(payload)

    assert payload["user"]["password"] == "p@ssw0rd"
    assert redacted["user"]["password"] == "<redacted>"
    assert redacted["items"][0]["token"] == "<redacted>"


def test_redact_header_like_structures() -> None:
    redactor = Redactor()

    headers = {
        "Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.aaaa.bbbb",
        "Cookie": "sessionid=abc",
        "User-Agent": "pytest",
    }
    redacted_headers = redactor.redact(headers)
    assert redacted_headers["Authorization"] == "<redacted>"
    assert redacted_headers["Cookie"] == "<redacted>"
    assert redacted_headers["User-Agent"] == "pytest"

    header_pairs = [
        ("Authorization", "Bearer abcdefghijklmnop"),
        ("Accept", "application/json"),
    ]
    redacted_pairs = redactor.redact(header_pairs)
    assert redacted_pairs[0][1] == "<redacted>"
    assert redacted_pairs[1][1] == "application/json"


def test_redact_regex_matches_in_strings_and_bytes() -> None:
    redactor = Redactor()

    text = "calling https://example.com?token=ghp_abcdefghijklmnopqrstuvwxyz12345"
    redacted_text = redactor.redact(text)
    assert "ghp_" not in redacted_text
    assert "<redacted>" in redacted_text

    data = b"Authorization: Bearer abcdefghijklmnop"
    redacted_bytes = redactor.redact(data)
    assert isinstance(redacted_bytes, bytes)
    assert b"abcdef" not in redacted_bytes
    assert b"<redacted>" in redacted_bytes


def test_redact_never_throws_on_unknown_types() -> None:
    class Exploding:
        def __str__(self) -> str:  # pragma: no cover
            raise RuntimeError("boom")

        def __repr__(self) -> str:  # pragma: no cover
            raise RuntimeError("boom")

    redactor = Redactor()
    result = redactor.redact(Exploding())
    assert isinstance(result, str)

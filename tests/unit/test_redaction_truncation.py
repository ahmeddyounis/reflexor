from __future__ import annotations

import uuid

from reflexor.config import ReflexorSettings
from reflexor.observability.audit_sanitize import sanitize_for_audit, sanitize_tool_output
from reflexor.observability.redaction import Redactor
from reflexor.observability.truncation import TRUNCATION_MARKER, estimate_size_bytes


def test_redactor_key_and_regex_redaction_handles_nested_and_bytes() -> None:
    redactor = Redactor()

    obj = {
        "Password": "p@ssw0rd",
        "nested": [
            {"authorization": "Bearer abcdefghijklmnop"},
            {"apiKey": "sk-" + ("a" * 30)},
        ],
        "headers": [("Cookie", "sessionid=abc"), ("Accept", "application/json")],
    }

    redacted = redactor.redact(obj)
    assert redacted["Password"] == "<redacted>"
    assert redacted["nested"][0]["authorization"] == "<redacted>"
    assert redacted["nested"][1]["apiKey"] == "<redacted>"
    assert redacted["headers"][0][1] == "<redacted>"
    assert redacted["headers"][1][1] == "application/json"

    redacted_text = redactor.redact("Bearer abcdefghijklmnop")
    assert redacted_text == "Bearer <redacted>"

    redacted_bytes = redactor.redact(b"Authorization: Bearer abcdefghijklmnop")
    assert isinstance(redacted_bytes, bytes)
    assert b"abcdefgh" not in redacted_bytes
    assert b"<redacted>" in redacted_bytes


def test_redactor_respects_max_depth_and_max_items() -> None:
    shallow = Redactor(max_depth=1)
    deep = {"a": {"b": {"secret": "x"}}}
    assert shallow.redact(deep) == {"a": "<MAX_DEPTH>"}

    limited = Redactor(max_items=2)
    many = {"a": 1, "b": 2, "c": 3}
    redacted = limited.redact(many)
    assert "<TRUNCATED>" in redacted

    seq = ["a", "b", "c"]
    redacted_seq = limited.redact(seq)
    assert redacted_seq[-1] == "<TRUNCATED>"


def test_redactor_truncates_after_redaction() -> None:
    redactor = Redactor()
    secret = "sk-" + ("b" * 40)
    text = f"{secret} " + ("x" * 200)

    sanitized = redactor.redact(text, max_bytes=60)
    assert isinstance(sanitized, str)
    assert "sk-" not in sanitized
    assert TRUNCATION_MARKER in sanitized


def test_sanitizer_preserves_ids_and_applies_redaction_and_truncation() -> None:
    run_id = str(uuid.uuid4())
    event_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())
    tool_call_id = str(uuid.uuid4())

    packet = {
        "run_id": run_id,
        "event": {
            "event_id": event_id,
            "type": "webhook",
            "source": "tests",
            "received_at_ms": 1,
            "payload": {
                "authorization": "Bearer " + ("x" * 40),
                "notes": "n" * 500,
            },
        },
        "tasks": [
            {
                "task_id": task_id,
                "run_id": run_id,
                "name": "do-thing",
                "tool_call": {
                    "tool_call_id": tool_call_id,
                    "tool_name": "net.http",
                    "permission_scope": "net.http",
                    "idempotency_key": "k",
                    "args": {"api_key": "sk-" + ("b" * 30)},
                },
            }
        ],
        "tool_results": [
            {
                "tool_call_id": tool_call_id,
                "output": {
                    "set-cookie": "sessionid=abc",
                    "body": "y" * 2_000,
                },
            }
        ],
    }

    settings = ReflexorSettings(
        max_event_payload_bytes=120,
        max_tool_output_bytes=160,
        max_run_packet_bytes=700,
    )

    sanitized = sanitize_for_audit(packet, settings=settings)

    assert sanitized["run_id"] == run_id
    assert sanitized["event"]["event_id"] == event_id
    assert sanitized["tasks"][0]["task_id"] == task_id
    assert sanitized["tasks"][0]["tool_call"]["tool_call_id"] == tool_call_id
    assert sanitized["tool_results"][0]["tool_call_id"] == tool_call_id

    dump = str(sanitized)
    assert "sk-" not in dump
    assert "sessionid=abc" not in dump
    assert "<redacted>" in dump
    assert "<truncated>" in dump
    assert estimate_size_bytes(sanitized) <= settings.max_run_packet_bytes


def test_sanitize_tool_output_realistic_blob_is_safe_and_bounded() -> None:
    settings = ReflexorSettings(max_tool_output_bytes=220, max_run_packet_bytes=5_000)

    tool_output = {
        "status_code": 200,
        "headers": [
            ("Content-Type", "application/json"),
            ("Authorization", "Bearer abcdefghijklmnop"),
            ("Set-Cookie", "sessionid=abc"),
        ],
        "json": {"token": "ghp_" + ("a" * 30), "data": "x" * 1_000},
    }

    sanitized = sanitize_tool_output(tool_output, settings=settings)
    dump = str(sanitized)
    assert "<redacted>" in dump
    assert "<truncated>" in dump
    assert "sessionid=abc" not in dump
    assert estimate_size_bytes(sanitized) <= settings.max_tool_output_bytes

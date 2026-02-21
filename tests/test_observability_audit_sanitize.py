from __future__ import annotations

import uuid

from reflexor.config import ReflexorSettings
from reflexor.observability.audit_sanitize import sanitize_for_audit, sanitize_tool_output
from reflexor.observability.truncation import TRUNCATION_MARKER


def test_sanitize_tool_output_redacts_and_truncates() -> None:
    settings = ReflexorSettings(max_tool_output_bytes=40, max_run_packet_bytes=10_000)

    obj = {
        "token": "ghp_" + ("a" * 40),
        "data": "x" * 200,
    }
    sanitized = sanitize_tool_output(obj, settings=settings)

    assert sanitized["token"] == "<redacted>"
    assert TRUNCATION_MARKER in sanitized["data"]


def test_sanitize_for_audit_preserves_ids_and_sanitizes_payloads() -> None:
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
                "authorization": "Bearer " + ("x" * 50),
                "notes": "n" * 200,
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
                    "cookie": "sessionid=abc",
                    "body": "y" * 500,
                },
            }
        ],
    }

    settings = ReflexorSettings(
        max_event_payload_bytes=80,
        max_tool_output_bytes=80,
        max_run_packet_bytes=350,
    )

    sanitized = sanitize_for_audit(packet, settings=settings)

    assert sanitized["run_id"] == run_id
    assert sanitized["event"]["event_id"] == event_id
    assert sanitized["tasks"][0]["task_id"] == task_id
    assert sanitized["tasks"][0]["tool_call"]["tool_call_id"] == tool_call_id
    assert sanitized["tool_results"][0]["tool_call_id"] == tool_call_id

    payload_dump = str(sanitized)
    assert "sk-" not in payload_dump
    assert "Bearer " not in payload_dump
    assert "sessionid=abc" not in payload_dump
    assert TRUNCATION_MARKER in payload_dump

from __future__ import annotations

import io
import json
import logging

from reflexor.config import ReflexorSettings
from reflexor.observability.context import correlation_context, request_id_context
from reflexor.observability.logging import build_json_handler


def test_logging_injects_context_and_redacts_secrets_in_json_logs() -> None:
    settings = ReflexorSettings()
    stream = io.StringIO()
    handler = build_json_handler(settings=settings, stream=stream)

    logger = logging.getLogger("reflexor.tests.logging")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False

    secret = "sk-abcdefghijklmnopqrstuvwxyz1234567890"
    with request_id_context(request_id="req-1"):
        with correlation_context(event_id="evt", run_id="run", task_id="task", tool_call_id="tc"):
            logger.info(
                f"hello {secret}",
                extra={"payload": {"authorization": f"Bearer {secret}"}},
            )

    output = stream.getvalue().strip()
    payload = json.loads(output)

    assert secret not in output
    assert payload["request_id"] == "req-1"
    assert payload["event_id"] == "evt"
    assert payload["run_id"] == "run"
    assert payload["task_id"] == "task"
    assert payload["tool_call_id"] == "tc"
    assert payload["payload"]["authorization"] == "<redacted>"

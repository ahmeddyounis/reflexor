from __future__ import annotations

import io
import json
import logging

from reflexor.observability.context import correlation_context
from reflexor.observability.logging import build_json_handler


def test_logging_injects_correlation_ids_into_json_logs() -> None:
    stream = io.StringIO()
    handler = build_json_handler(stream=stream)

    logger = logging.getLogger("reflexor.tests.logging")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False

    with correlation_context(event_id="evt", run_id="run", task_id="task", tool_call_id="tc"):
        logger.info("hello")

    output = stream.getvalue().strip()
    payload = json.loads(output)

    assert payload["message"] == "hello"
    assert payload["event_id"] == "evt"
    assert payload["run_id"] == "run"
    assert payload["task_id"] == "task"
    assert payload["tool_call_id"] == "tc"

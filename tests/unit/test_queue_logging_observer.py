from __future__ import annotations

import io
import json
import logging

from reflexor.config import ReflexorSettings
from reflexor.observability.context import correlation_context, request_id_context
from reflexor.observability.logging import build_json_handler
from reflexor.observability.queue_observers import LoggingQueueObserver
from reflexor.orchestrator.queue.observer import QueueDequeueObservation


def test_logging_queue_observer_clears_stale_context_for_empty_dequeue() -> None:
    stream = io.StringIO()
    handler = build_json_handler(
        settings=ReflexorSettings(),
        stream=stream,
        level=logging.DEBUG,
    )
    logger = logging.getLogger("reflexor.tests.queue_observer")
    original_handlers = list(logger.handlers)
    original_level = logger.level
    original_propagate = logger.propagate
    logger.handlers = [handler]
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    try:
        observer = LoggingQueueObserver(logger=logger)
        with request_id_context("req-stale"):
            with correlation_context(event_id="evt", run_id="run", task_id="task"):
                observer.on_dequeue(
                    QueueDequeueObservation(
                        lease=None,
                        correlation_ids=None,
                        now_ms=0,
                        queue_depth=0,
                    )
                )
    finally:
        logger.handlers = original_handlers
        logger.setLevel(original_level)
        logger.propagate = original_propagate

    payload = json.loads(stream.getvalue().strip())
    assert payload["event_type"] == "queue.dequeue.empty"
    assert payload["request_id"] is None
    assert payload["event_id"] is None
    assert payload["run_id"] is None
    assert payload["task_id"] is None

from __future__ import annotations

import asyncio
import io
import json
import logging

import pytest

from reflexor.observability.context import (
    correlation_context,
    get_correlation_ids,
    set_correlation_ids,
)
from reflexor.observability.logging import build_json_handler


@pytest.fixture(autouse=True)
def _clear_correlation_ids() -> None:
    set_correlation_ids(event_id=None, run_id=None, task_id=None, tool_call_id=None)


def test_correlation_context_sets_and_restores_ids() -> None:
    set_correlation_ids(run_id="outer-run")
    assert get_correlation_ids()["run_id"] == "outer-run"

    with correlation_context(event_id="evt", run_id="inner-run", task_id="task", tool_call_id="tc"):
        assert get_correlation_ids() == {
            "event_id": "evt",
            "run_id": "inner-run",
            "task_id": "task",
            "tool_call_id": "tc",
        }

    assert get_correlation_ids() == {
        "event_id": None,
        "run_id": "outer-run",
        "task_id": None,
        "tool_call_id": None,
    }


@pytest.mark.asyncio
async def test_ids_propagate_across_await() -> None:
    with correlation_context(event_id="evt", run_id="run", task_id="task", tool_call_id="tc"):
        await asyncio.sleep(0)
        assert get_correlation_ids() == {
            "event_id": "evt",
            "run_id": "run",
            "task_id": "task",
            "tool_call_id": "tc",
        }


@pytest.mark.asyncio
async def test_context_is_isolated_across_concurrent_tasks() -> None:
    async def worker(run_id: str) -> dict[str, str | None]:
        with correlation_context(run_id=run_id):
            await asyncio.sleep(0)
            return get_correlation_ids()

    ids_a, ids_b = await asyncio.gather(worker("run-a"), worker("run-b"))
    assert ids_a["run_id"] == "run-a"
    assert ids_b["run_id"] == "run-b"
    assert get_correlation_ids()["run_id"] is None


def test_logging_output_includes_correlation_ids_when_set() -> None:
    stream = io.StringIO()
    handler = build_json_handler(stream=stream)

    logger = logging.getLogger("reflexor.tests.unit.logging")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False

    with correlation_context(event_id="evt", run_id="run", task_id="task", tool_call_id="tc"):
        logger.info("hello")

    payload = json.loads(stream.getvalue().strip())
    assert payload["message"] == "hello"
    assert payload["event_id"] == "evt"
    assert payload["run_id"] == "run"
    assert payload["task_id"] == "task"
    assert payload["tool_call_id"] == "tc"

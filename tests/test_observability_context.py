from __future__ import annotations

import asyncio

import pytest

from reflexor.observability.context import (
    correlation_context,
    get_correlation_ids,
    set_correlation_ids,
)


async def _probe_after_await() -> dict[str, str | None]:
    await asyncio.sleep(0)
    return get_correlation_ids()


def test_correlation_context_restores_previous_state() -> None:
    set_correlation_ids(run_id="r1")
    assert get_correlation_ids()["run_id"] == "r1"

    with correlation_context(event_id="e1", run_id="r2", task_id="t1"):
        ids = get_correlation_ids()
        assert ids["event_id"] == "e1"
        assert ids["run_id"] == "r2"
        assert ids["task_id"] == "t1"

    restored = get_correlation_ids()
    assert restored["event_id"] is None
    assert restored["run_id"] == "r1"
    assert restored["task_id"] is None


@pytest.mark.asyncio
async def test_context_propagates_across_await() -> None:
    with correlation_context(event_id="evt", run_id="run", task_id="task", tool_call_id="tc"):
        ids = await _probe_after_await()
        assert ids == {
            "event_id": "evt",
            "run_id": "run",
            "task_id": "task",
            "tool_call_id": "tc",
        }


@pytest.mark.asyncio
async def test_context_is_restored_after_context_manager_exits() -> None:
    set_correlation_ids(event_id="outer")

    with correlation_context(event_id="inner"):
        assert (await _probe_after_await())["event_id"] == "inner"

    assert (await _probe_after_await())["event_id"] == "outer"

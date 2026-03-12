from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from reflexor.domain.models_event import Event


def _load_benchmark_module() -> ModuleType:
    repo_root = Path(__file__).resolve().parents[2]
    module_path = repo_root / "scripts" / "benchmark_event_to_enqueue.py"
    spec = importlib.util.spec_from_file_location(
        "benchmark_event_to_enqueue_test_module",
        module_path,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_run_benchmark_succeeds_for_fast_task_mode() -> None:
    module: Any = _load_benchmark_module()

    report = await module._run_benchmark(events=3, concurrency=2, planner="off")

    assert report["ok"] is True
    assert report["planner"] == "off"
    latency = report["latency_ms"]
    assert isinstance(latency, dict)
    assert latency["count"] == 3


@pytest.mark.asyncio
async def test_run_benchmark_succeeds_for_planner_mode() -> None:
    module: Any = _load_benchmark_module()

    report = await module._run_benchmark(events=3, concurrency=2, planner="on")

    assert report["ok"] is True
    assert report["planner"] == "on"
    latency = report["latency_ms"]
    assert isinstance(latency, dict)
    assert latency["count"] == 3


@pytest.mark.asyncio
async def test_submit_events_caps_peak_concurrency_to_requested_limit() -> None:
    module: Any = _load_benchmark_module()

    class _TrackingEngine:
        def __init__(self) -> None:
            self.current = 0
            self.peak = 0

        async def handle_event(self, event: Event) -> None:
            _ = event
            self.current += 1
            self.peak = max(self.peak, self.current)
            try:
                await asyncio.sleep(0.01)
            finally:
                self.current -= 1

    events = [
        Event(type="bench", source="tests.benchmark", received_at_ms=123, payload={})
        for _ in range(8)
    ]
    expected_keys = {event.event_id: event.event_id for event in events}
    engine = _TrackingEngine()

    start_times = await module._submit_events(
        engine=engine,
        events=events,
        expected_keys=expected_keys,
        concurrency=3,
    )

    assert len(start_times) == len(events)
    assert engine.peak <= 3


def test_summarize_recent_run_failures_includes_policy_decisions() -> None:
    module: Any = _load_benchmark_module()

    summaries = module._summarize_recent_run_failures(
        [
            {
                "event": {"event_id": "evt-1"},
                "policy_decisions": [
                    {
                        "type": "plan_validation_error",
                        "message": "tool manifest permission_scope is not enabled",
                    }
                ],
            }
        ]
    )

    assert summaries == [
        "event_id=evt-1 plan_validation_error: tool manifest permission_scope is not enabled"
    ]

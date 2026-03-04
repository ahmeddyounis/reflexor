from __future__ import annotations

import asyncio

import pytest
from prometheus_client import CollectorRegistry, generate_latest

from reflexor.observability.metrics import (
    ReflexorMetrics,
    async_timer,
    counter,
    histogram,
)


def test_metric_helpers_register_once_and_are_incrementable() -> None:
    registry = CollectorRegistry(auto_describe=True)

    c1 = counter("unit_test_counter", labels=["k"], registry=registry)
    c2 = counter("unit_test_counter", labels=["k"], registry=registry)
    assert c1 is c2

    c1.labels(k="v").inc(3)

    h1 = histogram("unit_test_hist_seconds", buckets=[0.1, 0.2], registry=registry)
    h2 = histogram("unit_test_hist_seconds", buckets=[0.1, 0.2], registry=registry)
    assert h1 is h2

    h1.observe(0.15)

    text = generate_latest(registry).decode()
    assert 'unit_test_counter_total{k="v"} 3.0' in text
    assert "unit_test_hist_seconds_count 1.0" in text


def test_metric_helpers_reject_label_mismatch_and_kind_conflicts() -> None:
    registry = CollectorRegistry(auto_describe=True)

    counter("unit_test_conflict", labels=["a"], registry=registry)
    with pytest.raises(ValueError, match="labelnames mismatch"):
        counter("unit_test_conflict", labels=["b"], registry=registry)

    counter("unit_test_kind_conflict", labels=None, registry=registry)
    with pytest.raises(ValueError, match="already registered as"):
        histogram("unit_test_kind_conflict", registry=registry)


async def test_async_timer_observes_histogram() -> None:
    registry = CollectorRegistry(auto_describe=True)
    h = histogram("unit_test_timer_seconds", registry=registry)

    async with async_timer(h, labels=None):
        await asyncio.sleep(0)

    text = generate_latest(registry).decode()
    assert "unit_test_timer_seconds_count 1.0" in text


def test_metrics_registry_includes_core_names() -> None:
    metrics = ReflexorMetrics.build()
    text = generate_latest(metrics.registry).decode()

    assert "events_received_total" in text
    assert "event_to_enqueue_seconds" in text
    assert "planner_latency_seconds" in text
    assert "tool_latency_seconds" in text
    assert "tasks_completed_total" in text
    assert "executor_retries_total" in text
    assert "idempotency_cache_hits_total" in text
    assert "policy_decisions_total" in text
    assert "queue_depth" in text
    assert "queue_redeliver_total" in text
    assert "orchestrator_rejections_total" in text

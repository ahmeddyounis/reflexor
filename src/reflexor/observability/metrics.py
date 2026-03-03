"""Centralized Prometheus metrics definitions + helpers.

This module defines a dedicated Prometheus registry and a consistent set of metric objects.
It avoids scattered metric globals across layers and provides small helpers for
creating metrics and timing async operations.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from weakref import WeakKeyDictionary

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram


@dataclass(frozen=True, slots=True)
class _MetricDef:
    kind: str
    metric: Any
    labelnames: tuple[str, ...]
    buckets: tuple[float, ...] | None = None


_RegistryCache = dict[tuple[str, str], _MetricDef]

_CACHE: WeakKeyDictionary[CollectorRegistry, _RegistryCache] = WeakKeyDictionary()


def _registry_cache(registry: CollectorRegistry) -> dict[tuple[str, str], _MetricDef]:
    cache = _CACHE.get(registry)
    if cache is None:
        cache = {}
        _CACHE[registry] = cache
    return cache


def counter(
    name: str,
    labels: Sequence[str] | None = None,
    *,
    registry: CollectorRegistry,
    description: str | None = None,
) -> Counter:
    """Create or return a Counter registered in `registry`."""

    labelnames = tuple(str(label) for label in (labels or ()))
    key = ("counter", str(name))
    cache = _registry_cache(registry)

    existing = cache.get(key)
    if existing is not None:
        if existing.kind != "counter":
            raise ValueError(f"metric {name!r} already registered as {existing.kind}")
        if existing.labelnames != labelnames:
            raise ValueError(f"metric {name!r} labelnames mismatch")
        metric = existing.metric
        assert isinstance(metric, Counter)
        return metric

    metric = Counter(
        str(name),
        str(description or name),
        labelnames=list(labelnames),
        registry=registry,
    )
    cache[key] = _MetricDef(kind="counter", metric=metric, labelnames=labelnames)
    return metric


def gauge(
    name: str,
    labels: Sequence[str] | None = None,
    *,
    registry: CollectorRegistry,
    description: str | None = None,
) -> Gauge:
    """Create or return a Gauge registered in `registry`."""

    labelnames = tuple(str(label) for label in (labels or ()))
    key = ("gauge", str(name))
    cache = _registry_cache(registry)

    existing = cache.get(key)
    if existing is not None:
        if existing.kind != "gauge":
            raise ValueError(f"metric {name!r} already registered as {existing.kind}")
        if existing.labelnames != labelnames:
            raise ValueError(f"metric {name!r} labelnames mismatch")
        metric = existing.metric
        assert isinstance(metric, Gauge)
        return metric

    metric = Gauge(
        str(name),
        str(description or name),
        labelnames=list(labelnames),
        registry=registry,
    )
    cache[key] = _MetricDef(kind="gauge", metric=metric, labelnames=labelnames)
    return metric


def histogram(
    name: str,
    labels: Sequence[str] | None = None,
    buckets: Sequence[float] | None = None,
    *,
    registry: CollectorRegistry,
    description: str | None = None,
) -> Histogram:
    """Create or return a Histogram registered in `registry`."""

    labelnames = tuple(str(label) for label in (labels or ()))
    buckets_tuple = tuple(float(b) for b in buckets) if buckets is not None else None
    key = ("histogram", str(name))
    cache = _registry_cache(registry)

    existing = cache.get(key)
    if existing is not None:
        if existing.kind != "histogram":
            raise ValueError(f"metric {name!r} already registered as {existing.kind}")
        if existing.labelnames != labelnames:
            raise ValueError(f"metric {name!r} labelnames mismatch")
        if existing.buckets != buckets_tuple:
            raise ValueError(f"metric {name!r} buckets mismatch")
        metric = existing.metric
        assert isinstance(metric, Histogram)
        return metric

    if buckets_tuple is None:
        metric = Histogram(
            str(name),
            str(description or name),
            labelnames=list(labelnames),
            registry=registry,
        )
    else:
        metric = Histogram(
            str(name),
            str(description or name),
            labelnames=list(labelnames),
            buckets=list(buckets_tuple),
            registry=registry,
        )
    cache[key] = _MetricDef(
        kind="histogram",
        metric=metric,
        labelnames=labelnames,
        buckets=buckets_tuple,
    )
    return metric


@asynccontextmanager
async def async_timer(
    metric: Histogram, labels: Mapping[str, str] | None = None
) -> AsyncIterator[None]:
    """Async context manager that observes wall-clock time in seconds."""

    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed_s = time.perf_counter() - start
        if labels is None:
            metric.observe(elapsed_s)
        else:
            metric.labels(**{str(k): str(v) for k, v in labels.items()}).observe(elapsed_s)


@dataclass(frozen=True, slots=True)
class ReflexorMetrics:
    """Metrics for Reflexor, backed by a dedicated CollectorRegistry.

    A per-container registry is used to keep tests deterministic (no cross-test collisions).
    """

    registry: CollectorRegistry

    # Core metrics (shared across layers)
    events_received_total: Counter
    event_to_enqueue_seconds: Histogram
    planner_latency_seconds: Histogram
    tool_latency_seconds: Histogram
    tasks_completed_total: Counter
    executor_retries_total: Counter
    idempotency_cache_hits_total: Counter
    policy_decisions_total: Counter
    queue_depth: Gauge
    queue_redeliver_total: Counter
    orchestrator_rejections_total: Counter

    # API-facing metrics (still centralized here)
    event_ingest_latency_seconds: Histogram
    approvals_pending_total: Gauge
    api_requests_total: Counter

    @classmethod
    def build(cls, *, registry: CollectorRegistry | None = None) -> ReflexorMetrics:
        effective_registry = CollectorRegistry(auto_describe=True) if registry is None else registry

        return cls(
            registry=effective_registry,
            events_received_total=counter(
                "events_received",
                registry=effective_registry,
                description="Total events received by the API",
            ),
            event_to_enqueue_seconds=histogram(
                "event_to_enqueue_seconds",
                registry=effective_registry,
                description="Time from event receipt to task enqueue",
            ),
            planner_latency_seconds=histogram(
                "planner_latency_seconds",
                registry=effective_registry,
                description="Planner cycle latency in seconds",
            ),
            tool_latency_seconds=histogram(
                "tool_latency_seconds",
                labels=["tool_name", "ok"],
                registry=effective_registry,
                description="Tool execution latency in seconds",
            ),
            tasks_completed_total=counter(
                "tasks_completed",
                labels=["status"],
                registry=effective_registry,
                description="Tasks completed by status",
            ),
            executor_retries_total=counter(
                "executor_retries",
                labels=["tool_name", "error_code"],
                registry=effective_registry,
                description="Executor retries scheduled by tool/error code",
            ),
            idempotency_cache_hits_total=counter(
                "idempotency_cache_hits",
                registry=effective_registry,
                description="Total idempotency ledger cache hits",
            ),
            policy_decisions_total=counter(
                "policy_decisions",
                labels=["action", "reason_code"],
                registry=effective_registry,
                description="Policy decisions by action and reason code",
            ),
            queue_depth=gauge(
                "queue_depth",
                registry=effective_registry,
                description="Approximate queue depth (best-effort)",
            ),
            queue_redeliver_total=counter(
                "queue_redeliver",
                registry=effective_registry,
                description="Total redeliveries due to visibility timeout",
            ),
            orchestrator_rejections_total=counter(
                "orchestrator_rejections",
                labels=["reason"],
                registry=effective_registry,
                description="Orchestrator rejections by reason",
            ),
            event_ingest_latency_seconds=histogram(
                "event_ingest_latency_seconds",
                registry=effective_registry,
                description="Event ingestion request latency in seconds",
            ),
            approvals_pending_total=gauge(
                "approvals_pending_total",
                registry=effective_registry,
                description="Total approvals pending operator decision",
            ),
            api_requests_total=counter(
                "api_requests",
                labels=["method", "route", "status"],
                registry=effective_registry,
                description="Total API HTTP requests",
            ),
        )


__all__ = [
    "ReflexorMetrics",
    "async_timer",
    "counter",
    "gauge",
    "histogram",
]

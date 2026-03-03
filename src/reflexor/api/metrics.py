from __future__ import annotations

from dataclasses import dataclass

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram


@dataclass(frozen=True, slots=True)
class ApiMetrics:
    """Prometheus metrics for the API layer.

    A per-app registry is used to avoid cross-test collisions (duplicate timeseries)
    when multiple FastAPI apps/containers are constructed in the same process.
    """

    registry: CollectorRegistry

    events_received_total: Counter
    event_ingest_latency_seconds: Histogram
    approvals_pending_total: Gauge
    api_requests_total: Counter

    @classmethod
    def build(cls) -> ApiMetrics:
        registry = CollectorRegistry(auto_describe=True)

        events_received_total = Counter(
            "events_received",
            "Total events received by the API",
            registry=registry,
        )
        event_ingest_latency_seconds = Histogram(
            "event_ingest_latency_seconds",
            "Event ingestion request latency in seconds",
            registry=registry,
        )
        approvals_pending_total = Gauge(
            "approvals_pending_total",
            "Total approvals pending operator decision",
            registry=registry,
        )
        api_requests_total = Counter(
            "api_requests",
            "Total API HTTP requests",
            labelnames=["method", "route", "status"],
            registry=registry,
        )

        return cls(
            registry=registry,
            events_received_total=events_received_total,
            event_ingest_latency_seconds=event_ingest_latency_seconds,
            approvals_pending_total=approvals_pending_total,
            api_requests_total=api_requests_total,
        )


__all__ = ["ApiMetrics"]

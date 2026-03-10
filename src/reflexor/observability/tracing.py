from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from reflexor.config import ReflexorSettings

try:  # pragma: no cover - exercised in environments with otel installed
    from opentelemetry import propagate, trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import (
        BatchSpanProcessor,
        ConsoleSpanExporter,
        SimpleSpanProcessor,
    )

    try:  # pragma: no cover - optional exporter
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    except ImportError:  # pragma: no cover
        OTLPSpanExporter = None

    _OTEL_AVAILABLE = True
except ImportError:  # pragma: no cover
    propagate = None
    trace = None
    Resource = None
    TracerProvider = None
    BatchSpanProcessor = None
    ConsoleSpanExporter = None
    SimpleSpanProcessor = None
    OTLPSpanExporter = None
    _OTEL_AVAILABLE = False


@dataclass(frozen=True, slots=True)
class TracingStatus:
    enabled: bool
    configured: bool
    available: bool


_configured = False
_enabled = False


def configure_tracing(settings: ReflexorSettings) -> TracingStatus:
    global _configured, _enabled

    if not settings.otel_enabled:
        _enabled = False
        return TracingStatus(enabled=False, configured=False, available=_OTEL_AVAILABLE)
    if not _OTEL_AVAILABLE:
        _enabled = False
        return TracingStatus(enabled=True, configured=False, available=False)
    if _configured:
        _enabled = True
        return TracingStatus(enabled=True, configured=True, available=True)

    assert trace is not None
    assert Resource is not None
    assert TracerProvider is not None

    provider = TracerProvider(
        resource=Resource.create({"service.name": settings.otel_service_name})
    )

    endpoint = settings.otel_exporter_otlp_endpoint
    if endpoint and OTLPSpanExporter is not None and BatchSpanProcessor is not None:
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    elif (
        settings.otel_console_exporter
        and ConsoleSpanExporter is not None
        and SimpleSpanProcessor is not None
    ):
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))

    trace.set_tracer_provider(provider)
    _configured = True
    _enabled = True
    return TracingStatus(enabled=True, configured=True, available=True)


@contextmanager
def start_span(
    name: str,
    *,
    attributes: Mapping[str, object] | None = None,
    carrier: Mapping[str, str] | None = None,
):
    if not _OTEL_AVAILABLE or not _enabled:
        yield None
        return

    assert propagate is not None
    assert trace is not None

    tracer = trace.get_tracer("reflexor")
    context = None if carrier is None else propagate.extract(dict(carrier))
    with tracer.start_as_current_span(name, context=context) as span:
        if attributes is not None:
            for key, value in attributes.items():
                if value is None:
                    continue
                span.set_attribute(key, _coerce_attribute_value(value))
        yield span


def inject_trace_carrier() -> dict[str, str]:
    if not _OTEL_AVAILABLE or not _enabled:
        return {}
    assert propagate is not None
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    return carrier


def _coerce_attribute_value(value: object) -> Any:
    if isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


__all__ = ["TracingStatus", "configure_tracing", "inject_trace_carrier", "start_span"]

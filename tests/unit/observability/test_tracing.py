from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from reflexor.config import ReflexorSettings
from reflexor.observability import tracing


def test_configure_tracing_is_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tracing, "_configured", False)
    monkeypatch.setattr(tracing, "_enabled", False)

    status = tracing.configure_tracing(ReflexorSettings())

    assert status.enabled is False
    assert status.configured is False
    assert tracing.inject_trace_carrier() == {}


def test_start_span_is_safe_when_tracing_is_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tracing, "_enabled", False)

    with tracing.start_span("tests.span") as span:
        assert span is None


def test_normalize_trace_carrier_filters_and_trims_values() -> None:
    assert tracing.normalize_trace_carrier(
        {
            " traceparent ": " 00-abc-123-01 ",
            "tracestate": " vendor=value ",
            "blank": "   ",
            "ignored": 1,
            2: "ignored",
        }
    ) == {
        "traceparent": "00-abc-123-01",
        "tracestate": "vendor=value",
    }


def test_start_span_ignores_carrier_extract_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeSpan:
        def __init__(self) -> None:
            self.attributes: dict[str, object] = {}

        def set_attribute(self, key: str, value: object) -> None:
            self.attributes[key] = value

    class _FakeTracer:
        def __init__(self) -> None:
            self.started: list[tuple[str, object | None]] = []
            self.span = _FakeSpan()

        @contextmanager
        def start_as_current_span(
            self, name: str, context: object | None = None
        ) -> Iterator[_FakeSpan]:
            self.started.append((name, context))
            yield self.span

    class _FakeTrace:
        def __init__(self, tracer: _FakeTracer) -> None:
            self._tracer = tracer

        def get_tracer(self, name: str) -> _FakeTracer:
            assert name == "reflexor"
            return self._tracer

    class _BrokenPropagate:
        def extract(self, carrier: dict[str, str]) -> object:
            assert carrier == {"traceparent": "00-abc-123-01"}
            raise ValueError("bad carrier")

    fake_tracer = _FakeTracer()
    monkeypatch.setattr(tracing, "_OTEL_AVAILABLE", True)
    monkeypatch.setattr(tracing, "_enabled", True)
    monkeypatch.setattr(tracing, "trace", _FakeTrace(fake_tracer))
    monkeypatch.setattr(tracing, "propagate", _BrokenPropagate())

    with tracing.start_span(
        "tests.span",
        carrier={"traceparent": "00-abc-123-01"},
        attributes={"key": "value"},
    ) as span:
        assert span is fake_tracer.span

    assert fake_tracer.started == [("tests.span", None)]
    assert fake_tracer.span.attributes == {"key": "value"}

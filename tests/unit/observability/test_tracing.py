from __future__ import annotations

from reflexor.config import ReflexorSettings
from reflexor.observability import tracing


def test_configure_tracing_is_disabled_by_default(monkeypatch) -> None:
    monkeypatch.setattr(tracing, "_configured", False)
    monkeypatch.setattr(tracing, "_enabled", False)

    status = tracing.configure_tracing(ReflexorSettings())

    assert status.enabled is False
    assert status.configured is False
    assert tracing.inject_trace_carrier() == {}


def test_start_span_is_safe_when_tracing_is_disabled(monkeypatch) -> None:
    monkeypatch.setattr(tracing, "_enabled", False)

    with tracing.start_span("tests.span") as span:
        assert span is None

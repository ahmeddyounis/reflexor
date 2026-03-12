from __future__ import annotations

from reflexor.executor.service.core import _trace_carrier_from_lease
from reflexor.orchestrator.queue import Lease, TaskEnvelope


def test_trace_carrier_from_lease_extracts_only_string_headers() -> None:
    lease = Lease(
        envelope=TaskEnvelope(
            task_id="11111111-1111-4111-8111-111111111111",
            run_id="22222222-2222-4222-8222-222222222222",
            trace={"otel": {"traceparent": "00-abc-123-01", "not_used": 1}},
        ),
        leased_at_ms=0,
        visibility_timeout_s=30.0,
        attempt=0,
    )

    assert _trace_carrier_from_lease(lease) == {"traceparent": "00-abc-123-01"}


def test_trace_carrier_from_lease_trims_values_and_ignores_blank_headers() -> None:
    lease = Lease(
        envelope=TaskEnvelope(
            task_id="11111111-1111-4111-8111-111111111111",
            run_id="22222222-2222-4222-8222-222222222222",
            trace={
                "otel": {
                    " traceparent ": " 00-abc-123-01 ",
                    "tracestate": " vendor=value ",
                    "blank": "   ",
                    "ignored": 1,
                }
            },
        ),
        leased_at_ms=0,
        visibility_timeout_s=30.0,
        attempt=0,
    )

    assert _trace_carrier_from_lease(lease) == {
        "traceparent": "00-abc-123-01",
        "tracestate": "vendor=value",
    }

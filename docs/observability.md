# Observability

Reflexor’s observability surface is intentionally small and safe-by-default. It provides:

- Structured JSON logs (with correlation IDs + redaction/truncation).
- Prometheus metrics (`/metrics`) for basic health/perf tracking.
- An offline benchmark script for event → enqueue latency.

This doc describes what exists today (no dashboards/tracing are required).

## Logging (JSON)

Reflexor uses structured JSON logs (via `structlog` + stdlib logging). Logs include correlation
fields when available:

- `event_id`
- `run_id`
- `task_id`
- `tool_call_id`
- `request_id` (API only)

Common keys:

- `ts` — RFC3339 UTC timestamp
- `level` — `debug|info|warning|error|critical`
- `logger` — logger name
- `message` — log message

### Configuration

- `REFLEXOR_LOG_LEVEL` controls verbosity (`INFO` by default).

### Redaction and truncation

Before output, log payloads are sanitized:

- Common secret-bearing keys (`authorization`, `token`, `password`, etc.) are replaced with
  `<redacted>`.
- Common secret patterns (e.g. `Bearer …`, `sk-…`, JWTs) are redacted.
- Payloads are truncated to a bounded size to avoid runaway log volume. The log byte budget is
  bounded by `min(REFLEXOR_MAX_TOOL_OUTPUT_BYTES, REFLEXOR_MAX_RUN_PACKET_BYTES)`.

Even with redaction/truncation, treat logs as potentially sensitive and review before sharing.

## Correlation IDs

Correlation IDs are used to make logs/metrics/debugging output joinable across layers:

- `event_id`: stable per ingested event (UUID4).
- `run_id`: stable per run created by the orchestrator (UUID4).
- `task_id`: stable per queued task (UUID4).
- `tool_call_id`: stable per tool call (UUID4).
- `request_id`: per HTTP request (when running the API).

Debugging tip: if you have a `run_id`, search logs for that value to find the entire lifecycle of a
run across reflex/planning/executor components.

## Metrics (Prometheus)

When running the API server, Reflexor exposes a Prometheus-compatible endpoint:

- `GET /metrics`

Example:

```bash
reflexor run api
curl -s http://127.0.0.1:8000/metrics | head
```

Notes:

- Metrics are kept in a per-process registry (useful for tests and local profiling).
- `approvals_pending_total` is refreshed on each scrape (best-effort; `-1` means unknown).

### Metrics catalog

| Metric | Type | Labels | Meaning |
| --- | --- | --- | --- |
| `events_received_total` | counter | (none) | Events received by the orchestrator/API container. |
| `event_ingest_latency_seconds` | histogram | (none) | API event ingest request latency. |
| `event_to_enqueue_seconds` | histogram | (none) | Time from event receipt to first task enqueue (reflex fast-task path). |
| `planner_latency_seconds` | histogram | (none) | Planning cycle latency. |
| `tool_latency_seconds` | histogram | `tool_name`, `ok` | Tool execution latency (executor). |
| `tasks_completed_total` | counter | `status` | Tasks completed by terminal status (`succeeded|failed|canceled|…`). |
| `executor_retries_total` | counter | `tool_name`, `error_code` | Retry decisions emitted by the executor. |
| `idempotency_cache_hits_total` | counter | (none) | Idempotency ledger cache hits. |
| `policy_decisions_total` | counter | `action`, `reason_code` | Policy decisions (`allow|deny|require_approval`) by reason. |
| `queue_depth` | gauge | (none) | Approximate queue depth (best-effort). |
| `queue_redeliver_total` | counter | (none) | Redeliveries due to lease/visibility timeout. |
| `orchestrator_rejections_total` | counter | `reason` | Orchestrator rejections (e.g. `budget`, `validation`). |
| `api_requests_total` | counter | `method`, `route`, `status` | API request counts by route and status. |
| `approvals_pending_total` | gauge | (none) | Pending approvals (refreshed on scrape). |

## Benchmark: event submit → first enqueue

The script `scripts/benchmark_event_to_enqueue.py` measures the latency from the moment an event is
submitted to `OrchestratorEngine.handle_event(...)` until the first task envelope is enqueued.

Safety properties:
- No network calls.
- No database usage.
- No tool execution (tasks are enqueued only).
- Uses `InMemoryQueue` and a `MockTool` registry entry.

### Run (text)

```bash
python scripts/benchmark_event_to_enqueue.py --events 250 --concurrency 50 --planner off
```

### Run (JSON)

```bash
python scripts/benchmark_event_to_enqueue.py --events 250 --concurrency 50 --planner off --json
```

### Planning mode

`--planner on` routes events through the planning backlog (reflex returns `needs_planning`) and
measures event → enqueue latency including the planning cycle that produces one task per event.

```bash
python scripts/benchmark_event_to_enqueue.py --events 250 --concurrency 50 --planner on --json
```

## Future work (explicitly not implemented yet)

Potential future additions may include distributed tracing (e.g. OpenTelemetry) and dashboards. If
added, they should remain optional and safe-by-default.

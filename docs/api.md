# API

Reflexor includes a small FastAPI service for:

- Ingesting events (`POST /v1/events`) with idempotency via `dedupe_key`
- Operator/admin read paths for runs and tasks
- Human-in-the-loop approvals (approve/deny + requeue)

## Running locally

Run the API server:

```sh
uvicorn reflexor.api.app:create_app --factory --reload
```

OpenAPI / Swagger UI:

- `http://localhost:8000/docs`
- `http://localhost:8000/openapi.json`

### Important: in-memory queue limitation

The default queue backend is `inmemory`, which is **single-process only**. Running the API and the
worker/executor in separate processes will not share the queue.

For multi-process deployments, use the Redis Streams backend:

- Install: `pip install "reflexor[redis]"` (or `pip install -e ".[redis]"` from the repo)
- Configure:
  - `REFLEXOR_QUEUE_BACKEND=redis_streams`
  - `REFLEXOR_REDIS_URL=redis://...`

## Authentication

Admin endpoints use an API key header:

- Header: `X-API-Key: <key>`
- Setting: `REFLEXOR_ADMIN_API_KEY`
  - If unset: admin access is allowed in `dev` and denied in `prod`.
  - If set: the key is required.

Events can be public by default. To require admin auth for events:

- `REFLEXOR_EVENTS_REQUIRE_ADMIN=true`

## Request IDs

Every response includes `X-Request-ID`. Error responses also include `request_id` in the JSON
body.

## Pagination

List endpoints return a `Page[T]` object:

- Query params: `limit` (0–200) and `offset` (>= 0)
- Response: `{limit, offset, total, items}`

## Endpoints

### Health

- `GET /healthz`
  - Returns `{ok, version, profile, time_ms, db_ok, queue_ok, queue_backend}`
  - Returns `503` and `ok=false` if either DB or queue connectivity fails

### Metrics

- `GET /metrics`
  - Prometheus text format
  - Includes counters/histograms for request volume and event ingest, plus a gauge for pending
    approvals

### Events

- `POST /v1/events` (preferred) or `POST /events` (compat)
  - Creates (or dedupes) an event, triggers reflex handling, and returns `event_id` and `run_id`.
  - If `dedupe_key` already exists for `(source, dedupe_key)`, the API returns `200` with
    `duplicate=true` and the existing IDs.

Example:

```sh
curl -X POST http://localhost:8000/v1/events \\
  -H 'Content-Type: application/json' \\
  -d '{
    "type": "webhook",
    "source": "demo",
    "payload": {"hello": "world"},
    "dedupe_key": "demo:1"
  }'
```

### Runs (admin)

- `GET /v1/runs` (or `/runs`)
  - Query params: `limit`, `offset`, optional `status`, optional `since_ms`
- `GET /v1/runs/{run_id}` (or `/runs/{run_id}`)
  - Returns run summary plus the sanitized run packet (if present)

### Tasks (admin)

- `GET /v1/tasks` (or `/tasks`)
  - Query params: `limit`, `offset`, optional `run_id`, optional `status`

### Approvals (admin)

Approvals are created by the worker/executor when a queued tool call is evaluated by policy and
requires human approval.

Reflexor ships a CLI/API-first approval UX. There is no bundled standalone approval web UI; the
documented admin endpoints below are the supported operator surface.

- `GET /v1/approvals` (or `/approvals`)
  - Query params: `limit`, `offset`, optional `status`, optional `run_id`
- `GET /v1/approvals/pending`
  - Convenience view for pending approvals
- `POST /v1/approvals/{approval_id}/approve` (or `/approvals/{approval_id}/approve`)
  - Marks the approval as approved and requeues the associated task (idempotent)
- `POST /v1/approvals/{approval_id}/deny` (or `/approvals/{approval_id}/deny`)
  - Marks the approval as denied and transitions the task to a terminal state (idempotent)
- `POST /v1/approvals/{approval_id}/decision`
  - Single endpoint that accepts `{decision: approved|denied, decided_by?}`

Example (approve):

```sh
curl -X POST http://localhost:8000/v1/approvals/<approval_id>/approve \\
  -H 'Content-Type: application/json' \\
  -H 'X-API-Key: <admin_key>' \\
  -d '{"decided_by":"operator@example.com"}'
```

## Error responses

Errors use a stable shape:

```json
{
  "error_code": "validation_error",
  "message": "invalid request",
  "request_id": "…",
  "details": {"errors": []}
}
```

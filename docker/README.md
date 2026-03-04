# Docker Compose stack (Postgres + Redis Streams)

This directory contains a production-ish local stack for Reflexor v0.2:
- `api` (FastAPI)
- `worker` (queue consumer + executor)
- `postgres` (persistence)
- `redis` (Redis Streams queue backend)

Safety defaults:
- `REFLEXOR_DRY_RUN=true`
- allowlists are empty
- Postgres uses `POSTGRES_HOST_AUTH_METHOD=trust` to avoid embedding secrets in compose (local-only)

## Run

```bash
cd docker
docker compose up --build
```

Wait for the API to become healthy:

```bash
curl -sSf http://localhost:8000/healthz
```

## Submit an event (reflex → enqueue → worker executes)

The compose stack is configured with `REFLEXOR_REFLEX_RULES_PATH=/app/docker/reflex_rules.json`,
which maps `type=webhook` events to a safe `fs.list_dir` tool call.

```bash
curl -sS -X POST http://localhost:8000/events \
  -H 'content-type: application/json' \
  -d '{"type":"webhook","source":"docker","payload":{"hello":"world"},"dedupe_key":"demo-1","received_at_ms":1}'
```

You should see the worker process the queued task in the `docker compose` logs.

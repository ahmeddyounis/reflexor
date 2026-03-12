# Docker Compose stack (Postgres + Redis Streams)

This directory contains a production-shaped local stack for Reflexor:
- `api` (FastAPI)
- `worker` (queue consumer + executor)
- `postgres` (persistence)
- `redis` (Redis Streams queue backend)

Safety defaults:
- `REFLEXOR_PROFILE=prod` with `REFLEXOR_DRY_RUN=true`
- admin/event auth is enabled with local-only default credentials
- allowlists are empty
- Redis stream length is bounded (`REFLEXOR_REDIS_STREAM_MAXLEN=10000`)

This stack is for local verification only. For repo-owned production deployment assets, use
`deploy/k8s/` plus the guidance in `docs/production_v0.2.md`.

## Run

Optional: override the local-only default credentials first.

```bash
export REFLEXOR_ADMIN_API_KEY='change-me-local-admin-key'
export REFLEXOR_LOCAL_POSTGRES_PASSWORD='change-me-local-postgres-password'
```

```bash
cd docker
docker compose up --build
```

Wait for the API to become healthy:

```bash
curl -sSf http://localhost:8000/healthz
```

You can inspect the prod-shaped config report from the running API container:

```bash
docker compose exec api reflexor config validate --json
```

## Submit an event (reflex → enqueue → worker executes)

The compose stack is configured with `REFLEXOR_REFLEX_RULES_PATH=/app/docker/reflex_rules.json`,
which maps `type=webhook` events to a safe `fs.list_dir` tool call.

```bash
curl -sS -X POST http://localhost:8000/events \
  -H "X-API-Key: ${REFLEXOR_ADMIN_API_KEY:-reflexor-local-admin-key}" \
  -H 'content-type: application/json' \
  -d '{"type":"webhook","source":"docker","payload":{"hello":"world"},"dedupe_key":"demo-1","received_at_ms":1}'
```

You should see the worker process the queued task in the `docker compose` logs.

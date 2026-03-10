# Production deployment (v0.2): Postgres + Redis Streams

This doc describes a production-ish deployment shape for Reflexor v0.2 using:

- Postgres for persistence (`asyncpg` + SQLAlchemy async)
- Redis Streams for the durable queue backend (`redis.asyncio`)
- Separate API and worker processes

For a one-command local stack that matches this shape, see `docker/docker-compose.yml` and
`docker/README.md`.

## Architecture (recommended)

- **API**: FastAPI service that ingests events and enqueues tasks.
- **Worker**: long-running queue consumer that executes tasks and persists outcomes.
- **Postgres**: stores events, runs, tasks, tool calls, approvals, run packets, and memory items.
- **Redis**: stores queue state (streams + consumer groups + delayed ZSET).

## Safe defaults (do not disable lightly)

Reflexor settings are intentionally conservative:

- `REFLEXOR_DRY_RUN=true` by default.
- `REFLEXOR_ENABLED_SCOPES` defaults to empty (deny everything).
- Allowlists default to empty:
  - `REFLEXOR_HTTP_ALLOWED_DOMAINS=[]`
  - `REFLEXOR_WEBHOOK_ALLOWED_TARGETS=[]`
- In `REFLEXOR_PROFILE=prod`, setting `REFLEXOR_DRY_RUN=false` requires
  `REFLEXOR_ALLOW_SIDE_EFFECTS_IN_PROD=true` or settings validation fails fast.

Recommendation: keep `dry_run=true` until you can prove policy is correctly configured and
observability is in place.

## Minimal env example (v0.2)

This is the smallest environment configuration to run API + worker in separate processes with
Postgres + Redis Streams (fill in placeholders; do not commit real secrets).

```env
REFLEXOR_PROFILE=prod
REFLEXOR_DRY_RUN=true

# Storage (Postgres)
REFLEXOR_DATABASE_URL=postgresql+asyncpg://<user>:<pass>@<host>:5432/<db>

# Queue (Redis Streams)
REFLEXOR_QUEUE_BACKEND=redis_streams
REFLEXOR_REDIS_URL=redis://<host>:6379/0
REFLEXOR_REDIS_STREAM_KEY=reflexor:tasks
REFLEXOR_REDIS_CONSUMER_GROUP=reflexor
REFLEXOR_REDIS_DELAYED_ZSET_KEY=reflexor:tasks:delayed

# IMPORTANT: set a unique consumer name per worker process.
REFLEXOR_REDIS_CONSUMER_NAME=reflexor-worker-1

# Policy (start with a narrow allow surface)
REFLEXOR_ENABLED_SCOPES='["fs.read"]'
REFLEXOR_APPROVAL_REQUIRED_SCOPES='[]'
REFLEXOR_HTTP_ALLOWED_DOMAINS='[]'
REFLEXOR_WEBHOOK_ALLOWED_TARGETS='[]'
REFLEXOR_WORKSPACE_ROOT=/srv/reflexor/workspace

# Reflex routing (v0.2 convenience)
# Without a reflex rule that produces "fast tasks" (or custom planner wiring),
# events will not produce executable tasks.
REFLEXOR_REFLEX_RULES_PATH=/etc/reflexor/reflex_rules.json

# Planner (choose one backend)
REFLEXOR_PLANNER_BACKEND=openai_compatible
REFLEXOR_PLANNER_MODEL=gpt-4.1-mini
REFLEXOR_PLANNER_API_KEY=<secret>
REFLEXOR_PLANNER_BASE_URL=https://api.openai.com/v1
REFLEXOR_PLANNER_MAX_MEMORY_ITEMS=5

# Tracing (optional)
REFLEXOR_OTEL_ENABLED=false
REFLEXOR_OTEL_SERVICE_NAME=reflexor-api
# REFLEXOR_OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318/v1/traces
```

## Migrations / schema management

Run migrations before starting API/worker:

```sh
python scripts/db_upgrade.py --database-url "postgresql+asyncpg://user:pass@host:5432/db"
```

Or use `REFLEXOR_DATABASE_URL`:

```sh
export REFLEXOR_DATABASE_URL="postgresql+asyncpg://user:pass@host:5432/db"
python scripts/db_upgrade.py
```

More details: `docs/storage.md`.

## Postgres recommendations

Reflexor relies on SQLAlchemy connection pooling for Postgres (ignored for SQLite).

Settings:

- `REFLEXOR_DB_POOL_SIZE` (unset by default)
- `REFLEXOR_DB_MAX_OVERFLOW` (unset by default)
- `REFLEXOR_DB_POOL_TIMEOUT_S` (unset by default)
- `REFLEXOR_DB_POOL_PRE_PING` (default `true`)

Guidance:

- Prefer a small, predictable pool per process, and scale horizontally.
- Keep `pool_pre_ping` enabled (default) to avoid long-lived stale connections.
- Ensure the database user has permission to create/alter tables when running migrations.

## Redis Streams queue semantics (v0.2)

Reflexor’s durable queue backend is `redis_streams`.

### Consumer groups

- Tasks are added to `REFLEXOR_REDIS_STREAM_KEY` using `XADD`.
- Workers read using consumer group semantics (`XREADGROUP`).
- On startup, the queue ensures the stream and group exist (`XGROUP CREATE ... MKSTREAM`).

### Visibility timeout and redelivery

If a worker dequeues a task but crashes before `ack`, the message remains pending.

Before blocking on new messages, `dequeue()` attempts to reclaim idle pending work:

- Primary: `XAUTOCLAIM` (Redis 6.2+)
- Fallback: bounded `XPENDING` + `XCLAIM` scan (best-effort)

The effective reclaim threshold uses:

- The dequeue visibility timeout (`Queue.dequeue(timeout_s=...)`), and
- A minimum idle threshold: `REFLEXOR_REDIS_VISIBILITY_TIMEOUT_MS`

The worker uses `REFLEXOR_EXECUTOR_VISIBILITY_TIMEOUT_S` as the dequeue visibility timeout.

### Delayed scheduling

Reflexor supports delayed delivery for retries and scheduling:

- If `TaskEnvelope.available_at_ms` is in the future, the envelope is stored in a ZSET
  (`REFLEXOR_REDIS_DELAYED_ZSET_KEY`) scored by `available_at_ms`.
- Before reading new work, `dequeue()` promotes due ZSET members to the stream using a Lua script
  (atomic best-effort): `ZRANGEBYSCORE` → `ZREM` → `XADD`.

### `nack` behavior (requeue)

- `nack(lease, delay_s=0)` acks the current stream entry and re-enqueues a new envelope immediately.
- `nack(lease, delay_s>0)` acks the current entry and schedules a new envelope in the delayed ZSET.
- Requeued envelopes have a deterministic attempt increment (`attempt = previous_attempt + 1`).

### Scaling workers (consumer names)

Run as many worker processes as needed. For clean behavior and observability:

- Set a **unique** `REFLEXOR_REDIS_CONSUMER_NAME` per process (e.g., include hostname/pod name).
- All workers in the same deployment should share `REFLEXOR_REDIS_CONSUMER_GROUP`.

### Stream growth and trimming

Acking removes messages from the consumer group’s pending set but does not delete stream entries.
To bound stream length, set:

- `REFLEXOR_REDIS_STREAM_MAXLEN` (approximate trimming).

## Readiness / health checks

- `GET /healthz` returns:
  - `db_ok` (DB connectivity)
  - `queue_ok` (Redis `PING` when using `redis_streams`)
  - `queue_backend`
- The endpoint returns `503` when either dependency is unreachable (`ok=false`).

## Ops checklist (v0.2)

- Run migrations on deploy (`scripts/db_upgrade.py`).
- Keep `REFLEXOR_DRY_RUN=true` until policy is configured and verified.
- Start with minimal scopes in `REFLEXOR_ENABLED_SCOPES` and keep allowlists empty until needed.
- Use `REFLEXOR_EVENTS_REQUIRE_ADMIN=true` + `REFLEXOR_ADMIN_API_KEY` (or an API gateway) for
  ingestion protection.
- Ensure each worker has a unique `REFLEXOR_REDIS_CONSUMER_NAME`.
- Set `REFLEXOR_REDIS_STREAM_MAXLEN` if you need bounded Redis storage.
- Use `/healthz` for readiness gates; use `docs/observability.md` for metrics/log context.

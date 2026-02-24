# Storage (SQLite) & Migrations

Reflexor currently uses SQLite (via SQLAlchemy async) as its MVP persistence layer. The database is
intended to store **audit/replay artifacts** (run packets) and **execution state** (runs, tasks,
tool calls, approvals) while keeping the domain layer pure.

This schema is still evolving and should be treated as internal until explicitly versioned as a
public contract.

## Schema overview

### `events`

Incoming events (webhooks, internal ticks, etc.).

- Primary key: `event_id` (UUID string)
- Key fields: `type`, `source`, `received_at_ms`, `payload` (JSON), `dedupe_key` (nullable)
- Indexes:
  - `ix_events_type` on `type`
  - `ux_events_source_dedupe_key` unique on (`source`, `dedupe_key`)

**Event dedupe:** when `dedupe_key` is set, `reflexor.storage.EventRepo.create_or_get_by_dedupe(...)`
enforces idempotency using the unique index on (`source`, `dedupe_key`).

### `runs`

Run metadata (separate from `run_packets` blobs).

- Primary key: `run_id` (UUID string)
- Key fields: `parent_run_id` (nullable), `created_at_ms`, `started_at_ms` (nullable),
  `completed_at_ms` (nullable)
- Indexes:
  - `ix_runs_created_at_ms` on `created_at_ms`

Note: a run “status” is not currently stored as a column; it is derived by read-side queries from
task counts/states.

### `tool_calls`

Tool invocation requests and their lifecycle state.

- Primary key: `tool_call_id` (UUID string)
- Key fields: `tool_name`, `args` (JSON), `permission_scope`, `idempotency_key`, `status`,
  timestamps (`created_at_ms`, `started_at_ms`, `completed_at_ms`), optional `result_ref`
- Indexes:
  - `ix_tool_calls_idempotency_key` on `idempotency_key`

### `tasks`

Executable units (typically 1 task → 1 tool call, but the model allows `tool_call_id` to be null).

- Primary key: `task_id` (UUID string)
- Foreign keys:
  - `run_id` → `runs.run_id`
  - `tool_call_id` (nullable) → `tool_calls.tool_call_id`
- Key fields: `name`, `status`, `attempts`, `max_attempts`, `timeout_s`, `depends_on` (JSON),
  timestamps, `labels` (JSON), `metadata` (JSON)
- Indexes:
  - `ix_tasks_run_id` on `run_id`
  - `ix_tasks_status` on `status`

### `approvals`

Human-in-the-loop approvals for policy-gated tool calls.

- Primary key: `approval_id` (UUID string)
- Foreign keys:
  - `run_id` → `runs.run_id`
  - `task_id` → `tasks.task_id`
  - `tool_call_id` → `tool_calls.tool_call_id`
- Key fields: `status`, `created_at_ms`, optional decision fields (`decided_at_ms`, `decided_by`),
  `payload_hash`, `preview`
- Indexes:
  - `ix_approvals_status` on `status`

### `run_packets`

Sanitized audit/replay envelope JSON for a run.

- Primary key / foreign key: `run_id` → `runs.run_id`
- Key fields: `created_at_ms`, `packet_version`, `packet` (JSON)

**Sanitation guarantee:** `run_packets.packet` is persisted only after applying
`reflexor.observability.audit_sanitize.sanitize_for_audit(...)`, which performs:

- Key-based + regex-based redaction (e.g., Authorization/Bearer tokens)
- Deterministic truncation with `<truncated>` markers to enforce size limits

## Retention & growth expectations

There is currently **no automatic retention/TTL**: the SQLite file will grow over time as events,
runs, and run packets accumulate.

Operational guidance today:

- Treat the DB as an **audit/debugging store** (sanitized, but still potentially large).
- For local development, it is safe to delete the SQLite file and re-run migrations.
- Size caps for persisted packets are controlled by settings (see `docs/configuration.md`), but a
full retention policy will be added later.

## Migrations workflow (Alembic)

### Configure the database URL

Alembic reads the database URL from:

- `REFLEXOR_DATABASE_URL` (preferred), or
- `alembic.ini` (`sqlalchemy.url`) as a fallback

Example:

```sh
export REFLEXOR_DATABASE_URL="sqlite+aiosqlite:///./reflexor.db"
```

### Apply migrations

From the repo root:

```sh
make db-upgrade
```

Alternatively (same behavior, without `make`):

```sh
python -m reflexor.infra.db.migrate upgrade
```

If you prefer using Alembic directly:

```sh
alembic upgrade head
```

### Create a new migration (developer workflow)

1. Update ORM models in `src/reflexor/infra/db/models.py`.
2. Generate a revision:

```sh
alembic revision --autogenerate -m "describe change"
```

3. Review and edit the new file under `alembic/versions/`.
4. Apply:

```sh
make db-upgrade
```

### Troubleshooting

- `ValueError: database_url must be non-empty`:
  set `REFLEXOR_DATABASE_URL` or ensure `alembic.ini` has `sqlalchemy.url`.
- “No such table …” at runtime:
  run `alembic upgrade head` against the target DB.
- Smoke test:
  `pytest tests/integration/test_migrations.py`.

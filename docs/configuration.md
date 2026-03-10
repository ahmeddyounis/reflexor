# Configuration & Profiles

Reflexor uses `pydantic-settings` to load runtime configuration from environment variables with the
`REFLEXOR_` prefix.

Defaults are intentionally conservative (deny-by-default, dry-run enabled).

See `.env.template` for a safe local starting point.

## Loading settings

- Import root: `reflexor.config.ReflexorSettings`
- Cached accessor: `reflexor.config.get_settings()` (uses `functools.lru_cache`)
- Test helper: `reflexor.config.clear_settings_cache()`

Reflexor does **not** auto-load `.env` files yet. If you want to load one, call
`reflexor.config.load_env_file()` (requires optional dependency `python-dotenv`, install with
`pip install -e ".[dotenv]"`).

### List parsing

List-valued settings accept either:

- JSON array strings, e.g. `REFLEXOR_ENABLED_SCOPES='["fs.read","net.http"]'`, or
- Comma-separated strings, e.g. `REFLEXOR_ENABLED_SCOPES='fs.read,net.http'`.

### Dict parsing

Dict-valued settings accept either:

- JSON object strings, e.g. `REFLEXOR_EXECUTOR_PER_TOOL_CONCURRENCY='{"echo":5,"other":2}'`, or
- Comma-separated pairs, e.g. `REFLEXOR_EXECUTOR_PER_TOOL_CONCURRENCY='echo=5,other=2'`.

## Tool plugins (entry points)

Tool discovery via Python entry points is disabled by default.

- `REFLEXOR_ENABLE_TOOL_ENTRYPOINTS` (default `false`)
  - When `true`, Reflexor discovers tools from the `reflexor.tools` entry point group.
- `REFLEXOR_ALLOW_UNSUPPORTED_TOOLS` (default `false`, dev-only)
  - When `true` in `dev`, tools with unsupported `ToolManifest.sdk_version` are allowed with a
    warning.
  - In `prod`, settings validation rejects `REFLEXOR_ALLOW_UNSUPPORTED_TOOLS=true`.
- `REFLEXOR_TRUSTED_TOOL_PACKAGES` (default `[]`)
  - Allowlist of distribution names (packages) that are allowed to provide tools.
  - In `prod`, when this allowlist is non-empty, only tools from these packages are loaded.
  - Names are normalized (case-insensitive; `-`/`_`/`.` treated equivalently).
- `REFLEXOR_BLOCKED_TOOL_PACKAGES` (default `[]`)
  - Denylist of distribution names that are refused during discovery (denylist always wins).
  - Names are normalized (case-insensitive; `-`/`_`/`.` treated equivalently).

## Profiles

`REFLEXOR_PROFILE` supports:

- `dev` (default)
- `prod`

## Reflex routing

- `REFLEXOR_REFLEX_RULES_PATH` (default unset)
  - Optional path to reflex rules.
  - Supports `.json`, `.yaml`, and `.yml`.
  - When unset, the default router falls back to `needs_planning`.

## API authentication (admin)

The API supports a lightweight admin API key check for "admin" endpoints (runs, tasks, approvals).

- `REFLEXOR_ADMIN_API_KEY` (default unset)
  - If unset: admin endpoints are allowed in `dev` and denied in `prod`.
  - If set: require header `X-API-Key` to match.
- `REFLEXOR_EVENTS_REQUIRE_ADMIN` (default `false`)
  - If `true`, `/v1/events` also requires admin auth.

### Prod safety latch

In `prod`, disabling dry-run requires an explicit acknowledgement:

- `REFLEXOR_PROFILE=prod`
- `REFLEXOR_DRY_RUN=false`
- `REFLEXOR_ALLOW_SIDE_EFFECTS_IN_PROD=true` (required)

If the acknowledgement flag is missing, settings validation fails fast.

## Permission scopes (deny-by-default)

Scopes are stable strings used for policy checks. Configuration rejects unknown scopes.

Canonical scopes:

| Scope | Meaning |
| --- | --- |
| `net.http` | Allow outbound HTTP(S) requests (subject to allowlists). |
| `fs.read` | Allow reading files under the workspace root. |
| `fs.write` | Allow writing/modifying files under the workspace root. |
| `webhook.emit` | Allow emitting configured webhooks. |

Settings:

- `REFLEXOR_ENABLED_SCOPES` (default `[]`)
  - Empty means “deny everything”.
- `REFLEXOR_APPROVAL_REQUIRED_SCOPES` (default `[]`)
  - Must be a subset of `enabled_scopes`.
  - Enforced by the policy layer for tool execution (see `docs/policy.md`).

## Allowlists

These are **normalized** during settings validation to prevent common footguns:

- Trim whitespace; normalize case (domains/hostnames lowercased).
- Reject wildcards by default (`REFLEXOR_ALLOW_WILDCARDS=false`).
- Reject raw IP literals (e.g. `127.0.0.1`) for domains and webhook hosts.

### HTTP allowed domains

- Env var: `REFLEXOR_HTTP_ALLOWED_DOMAINS`
- Values must be hostnames only (no scheme, path, credentials, or port).
- Optional wildcard support (only leading `*.`), gated by `REFLEXOR_ALLOW_WILDCARDS=true`.

Examples:

- `["example.com","api.example.com"]`
- With wildcard enabled: `["*.example.com"]`

### Webhook allowed targets

- Env var: `REFLEXOR_WEBHOOK_ALLOWED_TARGETS`
- Values must be `http` or `https` URLs.
- Credentials are rejected (no `user:pass@host`).
- Optional hostname wildcard support (only leading `*.`), gated by `REFLEXOR_ALLOW_WILDCARDS=true`.

Example:

- `["https://hooks.example.com/path"]`

### Optional DNS resolution (anti-rebinding)

By default, allowlist and SSRF checks are purely **syntactic** (they validate/normalize the URL and
hostnames) and do not require DNS.

For additional defense-in-depth against DNS rebinding (where an allowlisted hostname later resolves
to a private/loopback/link-local/reserved IP), you can opt in to DNS resolution checks:

- `REFLEXOR_NET_SAFETY_RESOLVE_DNS` (default `false`)
- `REFLEXOR_NET_SAFETY_DNS_TIMEOUT_S` (default `0.5`)

Tradeoffs:

- Adds DNS lookups and latency to outbound requests.
- Blocks requests when DNS is unavailable/slow (fails closed on timeout).
- Best-effort: DNS can change between the check and the actual connection.

## Workspace root

- Env var: `REFLEXOR_WORKSPACE_ROOT`
- Normalized to an **absolute** path:
  - `~` is expanded
  - relative paths resolve against the **current working directory** of the running process
- Validation requires:
  - if the path exists, it must be a directory
  - if it does not exist, it must be creatable under an existing writable+executable ancestor

## Database defaults

These settings define how Reflexor will connect to its persistence layer (SQLite by default).

- `REFLEXOR_DATABASE_URL` (default `sqlite+aiosqlite:///./reflexor.db`)
  - Designed to be swappable later (e.g., to `postgresql+asyncpg://...`).
- `REFLEXOR_DB_ECHO` (default `false`)
  - Enables SQLAlchemy engine SQL logging when the database layer is wired.
- `REFLEXOR_DB_POOL_SIZE` (default unset)
  - Pool tuning for non-SQLite backends. Ignored for SQLite.
- `REFLEXOR_DB_MAX_OVERFLOW` (default unset)
  - Pool tuning for non-SQLite backends. Ignored for SQLite.
- `REFLEXOR_DB_POOL_TIMEOUT_S` (default unset)
  - Pool tuning for non-SQLite backends. Ignored for SQLite.
- `REFLEXOR_DB_POOL_PRE_PING` (default `true`)
  - Enables SQLAlchemy `pool_pre_ping` for non-SQLite backends. Ignored for SQLite.

## Size limits (bytes)

These caps are used by observability utilities to avoid runaway log/audit payload sizes:

- `REFLEXOR_MAX_EVENT_PAYLOAD_BYTES` (default `64000`)
- `REFLEXOR_MAX_TOOL_OUTPUT_BYTES` (default `64000`)
- `REFLEXOR_MAX_RUN_PACKET_BYTES` (default `512000`)

## Queue defaults

- `REFLEXOR_QUEUE_BACKEND` (default `inmemory`)
  - Selects the queue backend implementation.
  - Supported values: `inmemory` | `redis_streams`.
- `REFLEXOR_QUEUE_VISIBILITY_TIMEOUT_S` (default `60`)
  - Default visibility timeout (seconds) used by queue backends when `Queue.dequeue()` is called
    without an explicit `timeout_s`.

### Redis Streams queue settings

When `REFLEXOR_QUEUE_BACKEND=redis_streams`, the following settings apply:

- `REFLEXOR_REDIS_URL` (default unset)
  - In `prod`, this is required when using the Redis Streams queue backend.
- `REFLEXOR_REDIS_STREAM_KEY` (default `reflexor:tasks`)
  - Redis stream key used for task envelopes.
- `REFLEXOR_REDIS_CONSUMER_GROUP` (default `reflexor`)
  - Redis consumer group name.
- `REFLEXOR_REDIS_CONSUMER_NAME` (default auto-generated)
  - Consumer name for this worker process.
- `REFLEXOR_REDIS_STREAM_MAXLEN` (default unset)
  - Optional approximate max length for stream trimming; must be > 0 if set.
- `REFLEXOR_REDIS_CLAIM_BATCH_SIZE` (default `50`)
  - Batch size for claim operations.
- `REFLEXOR_REDIS_PROMOTE_BATCH_SIZE` (default `50`)
  - Batch size for promoting delayed work.
- `REFLEXOR_REDIS_VISIBILITY_TIMEOUT_MS` (default `60000`)
  - Visibility timeout for claimed messages; must be > 0.
- `REFLEXOR_REDIS_DELAYED_ZSET_KEY` (default `reflexor:tasks:delayed`)
  - ZSET key used for delayed scheduling.

## Executor defaults

These settings shape worker/executor behavior (concurrency, leasing, and retry backoff).

- `REFLEXOR_EXECUTOR_MAX_CONCURRENCY` (default `50`)
  - Global cap on in-flight task executions.
- `REFLEXOR_EXECUTOR_PER_TOOL_CONCURRENCY` (default `{}`)
  - Optional per-tool concurrency overrides (must be `<= executor_max_concurrency`).
- `REFLEXOR_EXECUTOR_DEFAULT_TIMEOUT_S` (default `60`)
  - Default tool execution timeout (seconds) when a task/tool call does not specify one.
- `REFLEXOR_EXECUTOR_VISIBILITY_TIMEOUT_S` (default `60`)
  - Visibility timeout (seconds) used when leasing tasks from the queue.
  - Must be `>= executor_default_timeout_s` to avoid lease expiry during default-length work.
- Retry defaults (used by backoff strategies):
  - `REFLEXOR_EXECUTOR_RETRY_BASE_DELAY_S` (default `1`)
  - `REFLEXOR_EXECUTOR_RETRY_MAX_DELAY_S` (default `60`)
  - `REFLEXOR_EXECUTOR_RETRY_JITTER` (default `0`, fraction in `[0,1]`)

## Orchestrator defaults

These values shape how often the planner runs and how much work a single run can admit.

Planner backend:

- `REFLEXOR_PLANNER_BACKEND` (default `noop`)
  - Supported values: `noop` | `heuristic` | `openai_compatible`.
- `REFLEXOR_PLANNER_MODEL` (default unset)
  - Required when `planner_backend=openai_compatible`.
- `REFLEXOR_PLANNER_API_KEY` (default unset)
  - Optional bearer token for the OpenAI-compatible planner backend.
- `REFLEXOR_PLANNER_BASE_URL` (default `https://api.openai.com/v1`)
  - Normalized by trimming trailing `/`.
- `REFLEXOR_PLANNER_TIMEOUT_S` (default `30`)
- `REFLEXOR_PLANNER_TEMPERATURE` (default `0`)
  - Must be in `[0, 2]`.
- `REFLEXOR_PLANNER_SYSTEM_PROMPT` (default unset)
  - Optional override for the planner system prompt.
- `REFLEXOR_PLANNER_MAX_MEMORY_ITEMS` (default `5`)
  - Maximum number of memory summaries injected into a planning call.

Planner cadence:

- `REFLEXOR_PLANNER_INTERVAL_S` (default `60`)
  - Periodic planning tick (safety net).
- `REFLEXOR_PLANNER_DEBOUNCE_S` (default `2`)
  - Event-driven planning debounce window (coalesces bursts of events into one cycle).

Backlog and per-cycle limits:

- `REFLEXOR_EVENT_BACKLOG_MAX` (default `200`)
  - Maximum number of events buffered for planning.
- `REFLEXOR_MAX_EVENTS_PER_PLANNING_CYCLE` (default `50`)
  - Maximum number of backlog events consumed by a single planning cycle.

Budgets (per run):

- `REFLEXOR_MAX_TASKS_PER_RUN` (default `50`)
- `REFLEXOR_MAX_TOOL_CALLS_PER_RUN` (default `50`)
- `REFLEXOR_MAX_RUN_WALL_TIME_S` (default `30`)

Note: In `prod`, be cautious with very small planner cadence values (e.g., sub-second intervals),
which can cause excessive churn/cost. Validation enforces positivity but does not currently block
"extreme" cadences.

## Tracing

Tracing is optional and requires the OTel extra if you want live exporters:

```sh
pip install -e ".[otel]"
```

Settings:

- `REFLEXOR_OTEL_ENABLED` (default `false`)
- `REFLEXOR_OTEL_SERVICE_NAME` (default `reflexor`)
- `REFLEXOR_OTEL_EXPORTER_OTLP_ENDPOINT` (default unset)
- `REFLEXOR_OTEL_CONSOLE_EXPORTER` (default `false`)

## Settings reference

| Setting | Env var | Type | Default |
| --- | --- | --- | --- |
| `profile` | `REFLEXOR_PROFILE` | `dev` \| `prod` | `dev` |
| `dry_run` | `REFLEXOR_DRY_RUN` | bool | `true` |
| `allow_side_effects_in_prod` | `REFLEXOR_ALLOW_SIDE_EFFECTS_IN_PROD` | bool | `false` |
| `allow_wildcards` | `REFLEXOR_ALLOW_WILDCARDS` | bool | `false` |
| `admin_api_key` | `REFLEXOR_ADMIN_API_KEY` | str? | unset |
| `events_require_admin` | `REFLEXOR_EVENTS_REQUIRE_ADMIN` | bool | `false` |
| `reflex_rules_path` | `REFLEXOR_REFLEX_RULES_PATH` | path? | unset |
| `enabled_scopes` | `REFLEXOR_ENABLED_SCOPES` | list[str] | `[]` |
| `approval_required_scopes` | `REFLEXOR_APPROVAL_REQUIRED_SCOPES` | list[str] | `[]` |
| `http_allowed_domains` | `REFLEXOR_HTTP_ALLOWED_DOMAINS` | list[str] | `[]` |
| `webhook_allowed_targets` | `REFLEXOR_WEBHOOK_ALLOWED_TARGETS` | list[str] | `[]` |
| `net_safety_resolve_dns` | `REFLEXOR_NET_SAFETY_RESOLVE_DNS` | bool | `false` |
| `net_safety_dns_timeout_s` | `REFLEXOR_NET_SAFETY_DNS_TIMEOUT_S` | float | `0.5` |
| `workspace_root` | `REFLEXOR_WORKSPACE_ROOT` | path | CWD |
| `database_url` | `REFLEXOR_DATABASE_URL` | str | `sqlite+aiosqlite:///./reflexor.db` |
| `db_echo` | `REFLEXOR_DB_ECHO` | bool | `false` |
| `db_pool_size` | `REFLEXOR_DB_POOL_SIZE` | int? | unset |
| `db_max_overflow` | `REFLEXOR_DB_MAX_OVERFLOW` | int? | unset |
| `db_pool_timeout_s` | `REFLEXOR_DB_POOL_TIMEOUT_S` | float? | unset |
| `db_pool_pre_ping` | `REFLEXOR_DB_POOL_PRE_PING` | bool | `true` |
| `queue_backend` | `REFLEXOR_QUEUE_BACKEND` | `inmemory` \| `redis_streams` | `inmemory` |
| `queue_visibility_timeout_s` | `REFLEXOR_QUEUE_VISIBILITY_TIMEOUT_S` | float | `60` |
| `redis_url` | `REFLEXOR_REDIS_URL` | str? | unset |
| `redis_stream_key` | `REFLEXOR_REDIS_STREAM_KEY` | str | `reflexor:tasks` |
| `redis_consumer_group` | `REFLEXOR_REDIS_CONSUMER_GROUP` | str | `reflexor` |
| `redis_consumer_name` | `REFLEXOR_REDIS_CONSUMER_NAME` | str | auto |
| `redis_stream_maxlen` | `REFLEXOR_REDIS_STREAM_MAXLEN` | int? | unset |
| `redis_claim_batch_size` | `REFLEXOR_REDIS_CLAIM_BATCH_SIZE` | int | `50` |
| `redis_promote_batch_size` | `REFLEXOR_REDIS_PROMOTE_BATCH_SIZE` | int | `50` |
| `redis_visibility_timeout_ms` | `REFLEXOR_REDIS_VISIBILITY_TIMEOUT_MS` | int | `60000` |
| `redis_delayed_zset_key` | `REFLEXOR_REDIS_DELAYED_ZSET_KEY` | str | `reflexor:tasks:delayed` |
| `executor_max_concurrency` | `REFLEXOR_EXECUTOR_MAX_CONCURRENCY` | int | `50` |
| `executor_per_tool_concurrency` | `REFLEXOR_EXECUTOR_PER_TOOL_CONCURRENCY` | dict[str,int] | `{}` |
| `executor_default_timeout_s` | `REFLEXOR_EXECUTOR_DEFAULT_TIMEOUT_S` | float | `60` |
| `executor_visibility_timeout_s` | `REFLEXOR_EXECUTOR_VISIBILITY_TIMEOUT_S` | float | `60` |
| `executor_retry_base_delay_s` | `REFLEXOR_EXECUTOR_RETRY_BASE_DELAY_S` | float | `1` |
| `executor_retry_max_delay_s` | `REFLEXOR_EXECUTOR_RETRY_MAX_DELAY_S` | float | `60` |
| `executor_retry_jitter` | `REFLEXOR_EXECUTOR_RETRY_JITTER` | float | `0` |
| `planner_backend` | `REFLEXOR_PLANNER_BACKEND` | `noop` \| `heuristic` \| `openai_compatible` | `noop` |
| `planner_model` | `REFLEXOR_PLANNER_MODEL` | str? | unset |
| `planner_api_key` | `REFLEXOR_PLANNER_API_KEY` | str? | unset |
| `planner_base_url` | `REFLEXOR_PLANNER_BASE_URL` | str | `https://api.openai.com/v1` |
| `planner_timeout_s` | `REFLEXOR_PLANNER_TIMEOUT_S` | float | `30` |
| `planner_temperature` | `REFLEXOR_PLANNER_TEMPERATURE` | float | `0` |
| `planner_system_prompt` | `REFLEXOR_PLANNER_SYSTEM_PROMPT` | str? | unset |
| `planner_max_memory_items` | `REFLEXOR_PLANNER_MAX_MEMORY_ITEMS` | int | `5` |
| `planner_interval_s` | `REFLEXOR_PLANNER_INTERVAL_S` | float | `60` |
| `planner_debounce_s` | `REFLEXOR_PLANNER_DEBOUNCE_S` | float | `2` |
| `event_backlog_max` | `REFLEXOR_EVENT_BACKLOG_MAX` | int | `200` |
| `max_events_per_planning_cycle` | `REFLEXOR_MAX_EVENTS_PER_PLANNING_CYCLE` | int | `50` |
| `otel_enabled` | `REFLEXOR_OTEL_ENABLED` | bool | `false` |
| `otel_service_name` | `REFLEXOR_OTEL_SERVICE_NAME` | str | `reflexor` |
| `otel_exporter_otlp_endpoint` | `REFLEXOR_OTEL_EXPORTER_OTLP_ENDPOINT` | str? | unset |
| `otel_console_exporter` | `REFLEXOR_OTEL_CONSOLE_EXPORTER` | bool | `false` |
| `max_tasks_per_run` | `REFLEXOR_MAX_TASKS_PER_RUN` | int | `50` |
| `max_tool_calls_per_run` | `REFLEXOR_MAX_TOOL_CALLS_PER_RUN` | int | `50` |
| `max_run_wall_time_s` | `REFLEXOR_MAX_RUN_WALL_TIME_S` | float | `30` |
| `max_event_payload_bytes` | `REFLEXOR_MAX_EVENT_PAYLOAD_BYTES` | int | `64000` |
| `max_tool_output_bytes` | `REFLEXOR_MAX_TOOL_OUTPUT_BYTES` | int | `64000` |
| `max_run_packet_bytes` | `REFLEXOR_MAX_RUN_PACKET_BYTES` | int | `512000` |

# Upgrade to v1.0

This guide highlights the main configuration and operational changes when upgrading to v1.0.
Defaults remain conservative (dry-run enabled, scopes/allowlists empty) so upgrading does not
introduce side effects unless you explicitly enable them.

## Before you start

- If you use a persistent database, run migrations before starting upgraded services (see
  `docs/storage.md` and `scripts/db_upgrade.py`).
- If you run multiple processes (API + worker), use Redis Streams for the queue
  (`REFLEXOR_QUEUE_BACKEND=redis_streams`); `inmemory` is single-process only.

## New/expanded hardening controls

### Rate limiting (tool execution)

Rate limiting is disabled by default.

Settings:

- `REFLEXOR_RATE_LIMITS_ENABLED` (default `false`)
- `REFLEXOR_RATE_LIMIT_DEFAULT` (optional)
- `REFLEXOR_RATE_LIMIT_PER_TOOL` (optional)
- `REFLEXOR_RATE_LIMIT_PER_DESTINATION` (optional; key is hostname)
- `REFLEXOR_RATE_LIMIT_PER_RUN` (optional)

Specs use a token bucket model:

- `capacity`
- `refill_rate_per_s`
- `burst` (default `0`)

Example:

```bash
REFLEXOR_RATE_LIMITS_ENABLED=true
REFLEXOR_RATE_LIMIT_DEFAULT='{"capacity":10,"refill_rate_per_s":5,"burst":0}'
REFLEXOR_RATE_LIMIT_PER_TOOL='{"net.http":{"capacity":5,"refill_rate_per_s":1,"burst":2}}'
```

Behavior:

- When over limit, execution is delayed (not failed) and the task is requeued with a retry delay.
- Run packets record the delay reason code (e.g. `rate_limited`).

### Circuit breaker (tool execution)

Circuit breaking is enabled in the default API container wiring and delays tool calls when a
dependency is failing repeatedly (fail-fast + half-open recovery).

Notes:

- Thresholds are currently code-wired (see `src/reflexor/api/container.py`).
- Run packets record delay reason codes (e.g. `circuit_open`, `circuit_half_open`).

### Event suppression (runaway loop protection)

Event suppression is disabled by default. When enabled, it suppresses repeated events with the
same signature (type/source plus optional selected payload fields) to prevent cascades/loops.

Settings:

- `REFLEXOR_EVENT_SUPPRESSION_ENABLED` (default `false`)
- `REFLEXOR_EVENT_SUPPRESSION_WINDOW_S`
- `REFLEXOR_EVENT_SUPPRESSION_THRESHOLD`
- `REFLEXOR_EVENT_SUPPRESSION_TTL_S`
- `REFLEXOR_EVENT_SUPPRESSION_SIGNATURE_FIELDS` (optional; list of dot paths)

Behavior:

- Suppression state is persisted to the DB so it survives restarts.
- Operators can list/clear suppressions via the API/CLI.

### Subprocess sandbox for tools (opt-in)

Sandboxing is disabled by default. When enabled for specific tools, they run in a separate Python
subprocess with a stripped environment and strict timeouts.

Settings:

- `REFLEXOR_SANDBOX_ENABLED` (default `false`)
- `REFLEXOR_SANDBOX_TOOLS` (default `[]`; tool names to sandbox)
- `REFLEXOR_SANDBOX_ENV_ALLOWLIST` (default `[]`; empty means no env vars are passed through)
- `REFLEXOR_SANDBOX_MAX_MEMORY_MB` (optional; best-effort)
- `REFLEXOR_SANDBOX_PYTHON_EXECUTABLE` (optional)

Example:

```bash
REFLEXOR_SANDBOX_ENABLED=true
REFLEXOR_SANDBOX_TOOLS='["net.http","webhook.emit"]'
REFLEXOR_SANDBOX_ENV_ALLOWLIST='["PYTHONPATH"]'
```

### Tool plugins (Python entry points) + trust controls

Entry point discovery is disabled by default. When enabled, tools are loaded from the
`reflexor.tools` entry point group.

Settings:

- `REFLEXOR_ENABLE_TOOL_ENTRYPOINTS` (default `false`)
- `REFLEXOR_TRUSTED_TOOL_PACKAGES` (default `[]`)
- `REFLEXOR_BLOCKED_TOOL_PACKAGES` (default `[]`; denylist always wins)
- `REFLEXOR_ALLOW_UNSUPPORTED_TOOLS` (default `false`; dev-only)

Production guidance:

- Keep discovery off unless you need it.
- If enabling it in `prod`, set `REFLEXOR_TRUSTED_TOOL_PACKAGES` so only known distributions can
  provide tools.

## Observability changes (useful for tuning)

- Guard-related delays are visible in Prometheus metrics and in run packets.
- Metrics include counters for rate limiting, circuit breaker opens, and suppressed events, plus a
  `retry_after_seconds` histogram for delay durations.

See `docs/observability.md` for the metrics catalog and general debugging workflow.

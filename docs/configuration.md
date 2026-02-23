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

## Profiles

`REFLEXOR_PROFILE` supports:

- `dev` (default)
- `prod`

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

## Workspace root

- Env var: `REFLEXOR_WORKSPACE_ROOT`
- Normalized to an **absolute** path:
  - `~` is expanded
  - relative paths resolve against the **current working directory** of the running process
- Validation requires:
  - if the path exists, it must be a directory
  - if it does not exist, it must be creatable under an existing writable+executable ancestor

## Size limits (bytes)

These caps are used by observability utilities to avoid runaway log/audit payload sizes:

- `REFLEXOR_MAX_EVENT_PAYLOAD_BYTES` (default `64000`)
- `REFLEXOR_MAX_TOOL_OUTPUT_BYTES` (default `64000`)
- `REFLEXOR_MAX_RUN_PACKET_BYTES` (default `512000`)

## Queue defaults

- `REFLEXOR_QUEUE_BACKEND` (default `inmemory`)
  - Selects the queue backend implementation.
- `REFLEXOR_QUEUE_VISIBILITY_TIMEOUT_S` (default `60`)
  - Default visibility timeout (seconds) used by queue backends when `Queue.dequeue()` is called
    without an explicit `timeout_s`.

## Settings reference

| Setting | Env var | Type | Default |
| --- | --- | --- | --- |
| `profile` | `REFLEXOR_PROFILE` | `dev` \| `prod` | `dev` |
| `dry_run` | `REFLEXOR_DRY_RUN` | bool | `true` |
| `allow_side_effects_in_prod` | `REFLEXOR_ALLOW_SIDE_EFFECTS_IN_PROD` | bool | `false` |
| `allow_wildcards` | `REFLEXOR_ALLOW_WILDCARDS` | bool | `false` |
| `enabled_scopes` | `REFLEXOR_ENABLED_SCOPES` | list[str] | `[]` |
| `approval_required_scopes` | `REFLEXOR_APPROVAL_REQUIRED_SCOPES` | list[str] | `[]` |
| `http_allowed_domains` | `REFLEXOR_HTTP_ALLOWED_DOMAINS` | list[str] | `[]` |
| `webhook_allowed_targets` | `REFLEXOR_WEBHOOK_ALLOWED_TARGETS` | list[str] | `[]` |
| `workspace_root` | `REFLEXOR_WORKSPACE_ROOT` | path | CWD |
| `queue_backend` | `REFLEXOR_QUEUE_BACKEND` | `inmemory` | `inmemory` |
| `queue_visibility_timeout_s` | `REFLEXOR_QUEUE_VISIBILITY_TIMEOUT_S` | float | `60` |
| `max_event_payload_bytes` | `REFLEXOR_MAX_EVENT_PAYLOAD_BYTES` | int | `64000` |
| `max_tool_output_bytes` | `REFLEXOR_MAX_TOOL_OUTPUT_BYTES` | int | `64000` |
| `max_run_packet_bytes` | `REFLEXOR_MAX_RUN_PACKET_BYTES` | int | `512000` |

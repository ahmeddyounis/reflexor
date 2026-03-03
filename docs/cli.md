# CLI

Reflexor ships with a small Typer-based CLI (`reflexor`) intended for local development and basic
operator workflows.

The CLI supports:

- **Local mode** (default): in-process calls into the application services (no HTTP).
- **Remote mode**: HTTP calls to the FastAPI service when `REFLEXOR_API_URL` (or `--api-url`) is
  configured.

## Global options

These options apply to all commands:

- `--profile dev|prod`: override `REFLEXOR_PROFILE`.
- `--api-url <base_url>`: use remote API mode (same as `REFLEXOR_API_URL`).
- `--api-key <key>`: admin API key (sent as `X-API-Key` in remote mode; also overrides
  `REFLEXOR_ADMIN_API_KEY`).
- `--json`: machine-readable JSON output.
- `--pretty`: pretty-printed JSON (implies `--json`).
- `--yes`: skip safety confirmations (used by `approvals approve` in `prod`).

## Local vs remote mode

### Local mode (default)

If `REFLEXOR_API_URL` is **not** set, the CLI builds an in-process application container and calls
application services directly (no network).

Important caveat: the default queue backend is `inmemory`, which is **single-process only**. If you
run `submit-event` in one process and `run worker` in another, they will **not** share the queue.
For a full end-to-end demo today, prefer the integration tests (see below).

### Remote mode

If `REFLEXOR_API_URL` (or `--api-url`) is set, the CLI uses HTTP calls to the API:

- `submit-event` → `POST /v1/events`
- `runs`/`tasks`/`approvals` → corresponding read/command endpoints

Admin endpoints may require an API key depending on server settings (see `docs/api.md`).

## JSON output

All commands support `--json` and `--pretty`:

```sh
reflexor runs list --json
reflexor runs show <run_id> --pretty
```

In table/text mode (default), list commands render aligned tables.

## Commands

### Running services (dev)

- `reflexor run api [--host ...] [--port ...] [--reload/--no-reload]`
- `reflexor run worker [--concurrency N]`

Note: with the default `inmemory` queue, the API and worker only share a queue when running in the
same process (durable queue backends are not implemented yet).

### Event submission

- `reflexor submit-event --type <type> [--source <source>] [--payload <json>] [--payload-file <path>] [--dedupe-key <k>]`

Example:

```sh
reflexor submit-event --type webhook --source demo --payload '{"hello":"world"}' --json
```

### Runs

- `reflexor runs list [--status <status>] [--since-ms <ms>] [--limit N] [--offset N]`
- `reflexor runs show <run_id>`

### Tasks

- `reflexor tasks list [--run-id <run_id>] [--status <status>] [--limit N] [--offset N]`

### Approvals (human-in-the-loop)

- `reflexor approvals list [--pending-only] [--status <status>] [--run-id <run_id>] [--scope <scope>]`
- `reflexor approvals approve <approval_id> [--decided-by <who>]`
- `reflexor approvals deny <approval_id> [--decided-by <who>]`

Safety prompt:

- In `REFLEXOR_PROFILE=prod`, `approvals approve` requires `--yes` (or an interactive confirmation).

### Tools (registry info)

- `reflexor tools list`

Note: tool listing is currently **local-mode only**; remote mode returns a `not_supported` error.

### Config (effective settings)

- `reflexor config show`

This prints the effective `ReflexorSettings` with secrets redacted:

- `admin_api_key` is always redacted
- any URL password (e.g., `postgresql://user:pass@...`) is redacted

## End-to-end smoke demo (offline)

The repo includes an offline, deterministic CLI smoke test that runs local flows (including an
approval-required execution path) in a single process:

```sh
pytest -q tests/integration/test_cli_smoke.py
```


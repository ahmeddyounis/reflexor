# Replay

Reflexor supports exporting a persisted run packet to a sanitized JSON file and replaying it
offline. This is intended for debugging and sharing reproducible traces of a run without exposing
raw secrets.

Important: exported run packets are **redacted and truncated**, but you should still review the
output before posting publicly.

## Concepts

- **Run packet**: a bounded, sanitized record of an orchestrator run (event, decisions, tasks, tool
  results, policy decisions).
- **Export**: write a sanitized JSON file for a given `run_id`.
- **Import**: create a new run/run-packet from a previously exported file (links back via
  `parent_run_id`).
- **Replay**: create a new run linked to the exported packet and (optionally) execute tasks using
  mock tools.

## Local-mode only

`runs export` / `runs import` / `runs replay` are **local-mode only** today (the HTTP API does not
expose these endpoints yet). Ensure you are not using remote mode:

- Do **not** set `REFLEXOR_API_URL` (and do not pass `--api-url`).
- Point the CLI at the same DB you want to read/write via `REFLEXOR_DATABASE_URL`.

## Export

Export a run packet to a file:

```bash
reflexor runs export <run_id> --out ./run_packet.json --json
```

Export output is bounded by `REFLEXOR_MAX_RUN_PACKET_BYTES` and sanitized using Reflexor’s redaction
and truncation rules.

## Import

Import an exported run packet into your local DB:

```bash
reflexor runs import ./run_packet.json --json
```

This creates a new run with a new `run_id` and sets `parent_run_id` to the original captured run.
Inspect it:

```bash
reflexor runs show <imported_run_id> --json
```

## Replay

Replay creates a new run linked to the captured run packet and executes tasks according to a mode.
Replay always enforces `dry_run=true`.

```bash
reflexor runs replay ./run_packet.json --mode mock_tools_recorded --json
```

### Modes

Replay modes (use `--mode`):

- `dry_run_no_tools`: persist a replay run packet but do not execute any tools.
- `mock_tools_recorded`: execute tasks using mock tools that return recorded `ToolResult`s from the
  captured run packet (when present).
- `mock_tools_success`: execute tasks using mock tools that always return `ok=true`.

### Prod confirmation

If `REFLEXOR_PROFILE=prod`, `runs replay` requires `--yes` as a safety latch even though replay
forces dry-run:

```bash
reflexor --profile prod --yes runs replay ./run_packet.json --mode dry_run_no_tools --json
```

## Debugging workflow (offline)

A typical offline debugging loop looks like this:

1. Reproduce the issue and capture the `run_id` (from event submission or `runs list`).
2. Inspect the run and tasks:

   ```bash
   reflexor runs show <run_id> --pretty
   reflexor tasks list --run-id <run_id> --pretty
   reflexor approvals list --run-id <run_id> --pretty
   ```

3. Export a sanitized run packet and attach it to an issue (after review):

   ```bash
   reflexor runs export <run_id> --out ./run_packet.json --json
   ```

4. Replay locally to validate determinism:

   ```bash
   reflexor runs replay ./run_packet.json --mode mock_tools_recorded --json
   ```

## Safe sharing guidance

Run packet exports are intended to be safe to share, but “safe” is not the same as “permissionless
to publish”. Before posting an exported packet publicly:

1. **Review the file** for non-secret sensitive data (URLs, internal hostnames, filenames, customer
   identifiers, etc.).
2. **Search for known substrings** (API tokens, emails, project names) using tools like `rg`.
3. Prefer sharing a minimal reproduction if possible.

If you find anything sensitive, do not post the packet; instead, redact it further or regenerate
after removing the sensitive inputs.

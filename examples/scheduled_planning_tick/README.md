# Scheduled planning tick (in-process)

This walkthrough demonstrates periodic planning ticks generating tasks on a schedule.

Everything runs in-process, uses a local SQLite DB, and runs in dry-run mode.

## Run

From the repo root:

```bash
make venv
set -a; source examples/scheduled_planning_tick/.env.example; set +a
.venv/bin/python examples/scheduled_planning_tick/run.py
```

## Inspect (CLI)

```bash
.venv/bin/reflexor runs list --json
.venv/bin/reflexor tasks list --json
```

## Files

- `mock_tool.json` configures a safe mock tool used by the planner.
- `reflexor.db` is created locally when you run the script (ignored by git).

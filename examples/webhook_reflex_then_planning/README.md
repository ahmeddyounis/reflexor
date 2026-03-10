# Webhook reflex → planning follow-up (in-process)

This walkthrough simulates a webhook-style event ingestion that:
1) triggers an immediate reflex task, and
2) later triggers an event-driven planning cycle that generates additional tasks.

Everything runs in-process, uses a local SQLite DB, and runs in dry-run mode.

## Run

From the repo root:

```bash
make venv
set -a; source examples/webhook_reflex_then_planning/.env.example; set +a
.venv/bin/python examples/webhook_reflex_then_planning/run.py
```

## Inspect (CLI)

The script persists runs/tasks/run_packets in the SQLite DB configured above. You can inspect them:

```bash
.venv/bin/reflexor runs list --json
.venv/bin/reflexor runs show <run_id> --json
.venv/bin/reflexor tasks list --run-id <run_id> --json
```

Optional: export a run packet for sharing/replay:

```bash
.venv/bin/reflexor runs export <run_id> --out ./examples/webhook_reflex_then_planning/run_packet.json --json
```

## Files

- `reflex_rules.json` contains the reflex rules used by the script.
- `reflex_rules.yaml` contains the same rules in YAML form.
- `reflexor.db` is created locally when you run the script (ignored by git).

To exercise the planning path, send or adapt an event payload with planner hints such as
`planner_tasks` and `depends_on` so the heuristic planner emits a multi-step DAG.

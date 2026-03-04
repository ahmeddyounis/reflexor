# Human approval flow (in-process)

This walkthrough demonstrates an approval-required tool call flow:
1) event creates a task
2) worker/executor processes it and requires approval (tool is not executed)
3) approval is granted, which requeues the task
4) worker/executor processes the requeued task and executes the tool (still dry-run)

No real filesystem writes occur because `dry_run=true`.

## Run

From the repo root:

```bash
make venv
set -a; source examples/approval_flow/.env.example; set +a
.venv/bin/python examples/approval_flow/run.py
```

## Inspect (CLI)

```bash
.venv/bin/reflexor runs list --json
.venv/bin/reflexor tasks list --json
.venv/bin/reflexor approvals list --json
```

## Notes

- The tool used is `fs.write_text`, but in dry-run mode it only returns a summary and does not
  write to disk.
- The in-memory queue is single-process; this example runs orchestrator + executor in-process.

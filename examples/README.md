# Examples

These examples are safe-by-default:
- `dry_run=true`
- no network calls
- allowlists are empty unless you set them explicitly

Note: the current queue backend is in-memory (single-process). These walkthroughs run
everything in-process so you can see end-to-end behavior without side effects.

## Walkthroughs

- `examples/webhook_reflex_then_planning/` — webhook-like event → reflex task → later planning cycle
- `examples/scheduled_planning_tick/` — periodic planning tick that generates tasks
- `examples/approval_flow/` — approval-required tool call → approval → requeue → execution (dry-run)


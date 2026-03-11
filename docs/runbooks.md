# Runbooks

## API unhealthy

- Check `/healthz` and inspect `db_ok` / `queue_ok`.
- If `db_ok=false`, treat as a database incident first.
- If `queue_ok=false`, treat as a Redis incident first.
- Verify the latest deployment, image tag, and secrets rollout.

## Queue backlog growing

- Inspect `queue_depth`, `queue_redeliver_total`, and worker logs.
- Check whether approvals are blocking execution.
- Scale workers only after confirming downstream dependencies are healthy.
- If retries are spiking, inspect rate limits and circuit-breaker behavior.

## Planner degraded

- Check planner latency and provider errors.
- If the external planner is failing, switch to a safer backend or rely on reflex-only flows.
- Keep dry-run enabled until planner behavior is stable again.

## Database unavailable

- Stop rollout activity.
- Confirm Postgres connectivity and connection pool exhaustion.
- If the database was restored, rerun migrations and perform a smoke check before reopening traffic.

## Redis unavailable

- Expect worker dequeue failures and backlog growth.
- Restore Redis or point the deployment at a healthy replacement.
- Confirm workers reclaim and resume processing after recovery.

## Approval backlog

- Inspect pending approvals count and oldest pending age.
- Determine whether the backlog is expected policy gating or an operator-process failure.
- If policy configuration is too broad, revert to dry-run and adjust scopes/approval rules.

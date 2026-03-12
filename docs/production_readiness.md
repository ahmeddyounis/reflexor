# Production Readiness

This document defines the repo-owned production readiness bar for Reflexor.

## Required gates

1. Configuration and manifest validation:

```sh
reflexor --profile prod config validate --strict --json
python scripts/validate_k8s_manifests.py deploy/k8s
```

2. Runtime artifact validation:

```sh
docker build -f docker/Dockerfile -t reflexor:test .
```

3. Database safety:

- migration job succeeds against the target database,
- backup completes successfully,
- restore drill completes successfully against a non-production database,
- production preflight does not report a local Postgres endpoint.

4. Operational readiness:

- `/healthz` and `/metrics` are scraped,
- alerts are configured for DB, Redis, queue backlog, approval backlog, and worker failures,
- runbooks in `docs/runbooks.md` are available to operators.

## Rollout sequence

1. Deploy infrastructure and secrets.
2. Run migration job.
3. Deploy API and worker with `REFLEXOR_DRY_RUN=true`.
4. Verify health, queueing, metrics, and approval flow.
5. Enable narrowly scoped live traffic.
6. Disable dry-run only after approvals, alerts, and rollback procedures are proven.

## Acceptance checklist

- Postgres is the production database backend.
- Redis Streams is the shared queue backend.
- Admin and event ingress auth are enforced by the app or an upstream gateway.
- High-risk scopes are explicitly reviewed and approval-gated where required.
- Redis stream growth is bounded.
- Maintenance and backup schedules are active.
- Restore drills are documented and use `scripts/postgres_restore.py` against a non-production
  target by default.
- Operators can restore from backup and replay runs safely.

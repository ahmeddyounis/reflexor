# Disaster Recovery

This document covers the minimum disaster-recovery workflow for Reflexor.

## Backups

Create a Postgres backup from a trusted operator environment:

```sh
python scripts/postgres_backup.py \
  --database-url "$REFLEXOR_DATABASE_URL" \
  --output ./backups/reflexor-$(date +%F-%H%M).dump
```

Recommendations:

- store backups outside the cluster/node,
- encrypt them at rest,
- keep a retention policy,
- run backups at least daily for production deployments.

## Restore drill

Restore into a non-production database first:

```sh
python scripts/postgres_restore.py \
  --database-url "$REFLEXOR_DATABASE_URL" \
  --input ./backups/reflexor-2026-03-11-0100.dump \
  --format custom \
  --yes
```

After restore:

- run `python scripts/db_upgrade.py` if required,
- start one API pod and one worker pod in dry-run mode,
- confirm `/healthz`,
- verify recent runs, tasks, approvals, and memory items are present.

## Recovery priorities

1. Restore Postgres.
2. Restore Redis connectivity or provision a fresh Redis instance.
3. Run migrations.
4. Start API.
5. Start worker.
6. Re-enable maintenance jobs.

Redis queue state is reconstructible only for work that is still represented in durable storage;
acked stream entries and in-flight deliveries are not a substitute for database backups.

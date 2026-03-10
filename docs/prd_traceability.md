# PRD Traceability

This document reconciles the PRD language in `.tmp/PRD.md` with the implementation shipped in this
repository.

## Lifecycle mapping

The PRD uses user-facing lifecycle terms that are slightly broader than the internal state machine.

| PRD term | Implementation | Meaning |
| --- | --- | --- |
| `created` | `RunStatus.CREATED`; task persisted as `TaskStatus.PENDING` | Audit record exists, work has been admitted but not yet queued/running. |
| `planned` | task persisted as `TaskStatus.PENDING` with validated plan metadata | The planner has produced the task DAG, but dependency gates may still block execution. |
| `queued` | `TaskStatus.QUEUED` | Ready for worker execution. |
| `executing` | `TaskStatus.RUNNING` | Worker has started the tool call. |
| `completed` | `TaskStatus.SUCCEEDED` | Task finished successfully. |
| `failed` | `TaskStatus.FAILED` | Task attempt finished unsuccessfully. |
| `retry_scheduled` | `ExecutionDisposition.FAILED_TRANSIENT` plus queue `nack(..., delay_s=...)` | Reflexor represents retries via audit metadata and delayed requeue rather than a separate persisted status. |
| `canceled` | `TaskStatus.CANCELED` | Terminal cancellation, including blocked dependents after upstream failure/denial. |
| `archived` | persisted `run_packets`, `memory_items`, export/replay flows, and operator retention jobs | There is no separate runtime `archived` status. Archival is an operational retention concern. |

## Approval UX decision

The shipped operator interface for approvals is CLI/API-first.

Supported surfaces:

- API: `GET /approvals`, `POST /approvals/{approval_id}/approve`, `POST /approvals/{approval_id}/deny`
- CLI: `reflexor approvals list|approve|deny`

This satisfies the PRD’s v0.2 approval UX requirement via the documented CLI/API path. A bundled web
UI is intentionally deferred as optional future work rather than treated as the only acceptable UX.

## PRD capability map

### v0.1

- Reflex routing: JSON or YAML rules, `fast_tool`, `needs_planning`, `drop`, and `flag`
- Planning: structured plans with scopes, approvals, budget assertions, and provider-backed
  planner adapters
- Execution: dependency-aware queueing, retries, approvals, idempotency, and audit persistence

### v0.2

- Postgres + Alembic + Redis Streams deployment path
- `memory_items` summaries injected into planning context
- OpenTelemetry tracing hooks with queue propagation
- CLI/API approval workflow with filtering and idempotent decisions

### v1.0

- Stable tool manifest metadata with canonical input/output JSON Schemas
- Replay/export/import workflows
- Production/operator documentation for planner, memory, tracing, queueing, and policy

## Pointers

- Planning: [Planning](planning.md)
- Memory: [Memory](memory.md)
- Observability: [Observability](observability.md)
- Tools: [Tools](tools.md)
- Storage: [Storage](storage.md)

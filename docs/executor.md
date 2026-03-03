# Executor & Worker

This document describes Reflexor’s **task execution** pipeline:

- the long-running worker loop (`WorkerRunner`)
- the single-task execution pipeline (`ExecutorService`)
- how retries, idempotency caching, and approvals interact

Reflexor currently wires the worker/executor primarily in tests (there is no standalone worker CLI
yet). The behavior described here matches what exists in `src/reflexor/executor/` and
`src/reflexor/worker/`.

Code:

- Worker loop: `src/reflexor/worker/runner.py`
- Executor pipeline: `src/reflexor/executor/service.py`
- Retry classification/backoff: `src/reflexor/executor/retries.py`
- Idempotency ledger port: `src/reflexor/executor/idempotency.py`
- Policy enforcement boundary: `src/reflexor/security/policy/enforcement.py`
- Queue contract: `src/reflexor/orchestrator/queue/` (see `docs/queue.md`)

## WorkerRunner loop

`WorkerRunner` is a small runtime wrapper that:

1) `dequeue(...)`s a `Lease` from the queue
2) calls `ExecutorService.process_lease(lease)`
3) `ack(...)`s or `nack(...)`s the lease based on the returned execution outcome

Key inputs:

- `visibility_timeout_s`: passed as `timeout_s=` to `Queue.dequeue(...)`
  - configure via `REFLEXOR_EXECUTOR_VISIBILITY_TIMEOUT_S` when wiring a worker
- `dequeue_wait_s`: controls whether `dequeue(...)` is non-blocking or long-polls
  - default is a short poll (`0.5s`); tests often set `0.0` for deterministic behavior

Shutdown:

- The loop exits when `stop_event` is set, or when the queue raises `QueueClosed`.
- On unexpected exceptions from the executor, the worker best-effort `nack(...)`s with `delay_s=0`
  and `reason="worker_exception"`.

## Queue leasing semantics (ack / nack)

Workers operate on queue **leases**:

- `ack(lease)`: terminal; removes the envelope
- `nack(lease, delay_s=...)`: requeues the envelope (optionally delayed)

Reflexor’s queue contract is **at-least-once**: envelopes may be delivered more than once (e.g.,
lease expiration). See `docs/queue.md` for details (delays, visibility timeout, best-effort
ordering).

Important configuration relationship:

- If tool execution can take up to `timeout_s`, the queue visibility timeout should be **>=** that
  duration; otherwise, leases may expire and messages may be redelivered while a worker is still
  running.

## ExecutorService pipeline (single task)

The executor is application-layer code that executes **one** task at a time, end-to-end:

1) Load `Task` + `ToolCall` from storage
2) Short-circuit terminal states (already succeeded / canceled / waiting approval)
3) (Optional) Return a cached result from the idempotency ledger
4) Start execution (transition states) when allowed
5) Run the tool call through policy enforcement and the tool runner
6) Persist:
   - updated `Task` / `ToolCall` status
   - approval records (when required)
   - idempotency ledger records (for idempotent tools)
   - a sanitized audit entry into the run packet

The worker uses `ExecutorService.process_lease(...)`, which wraps `execute_task(...)` and applies
queue ack/nack scheduling.

## Status transitions

Domain lifecycle statuses are stable strings:

- `TaskStatus`: `pending`, `queued`, `running`, `waiting_approval`, `succeeded`, `failed`,
  `canceled`
- `ToolCallStatus`: `pending`, `running`, `succeeded`, `failed`, `denied`, `canceled`
- `ApprovalStatus`: `pending`, `approved`, `denied`, `expired`, `canceled`

Typical transitions:

### Allowed execution

- `Task.queued` + `ToolCall.pending`
  → `Task.running` + `ToolCall.running`
  → `Task.succeeded` + `ToolCall.succeeded` **or**
  → `Task.failed` + `ToolCall.failed`

Notes:

- `Task.attempts` increments when transitioning to `running`.
- Timestamps (`started_at_ms`, `completed_at_ms`) are set by the executor, using its injected clock.

### Policy denied

When policy returns `deny`, the executor marks the tool call as denied and the task as canceled:

- `ToolCall.denied`
- `Task.canceled`

This is intentional: policy denial is treated as a safety stop, not an execution failure.

### Approval required

When policy returns `require_approval` and the approval is not yet approved:

- an `Approval` row is created (idempotent by `tool_call_id`)
- the task transitions to `waiting_approval`
- the tool call remains `pending` (no start timestamps, no attempts increment)
- the worker **acks** the queue message (no retries)

To continue after approval:

- An external component must (a) set the approval to `approved`, then (b) transition the task from
  `waiting_approval` back to `queued`, and (c) enqueue a new `TaskEnvelope` for that task.
  (Auto-resume wiring is not implemented yet.)

### Cancellation

If a task is already `canceled` when leased, the worker:

- skips execution
- `ack(...)`s the lease

Cancellation is a terminal state; the executor does not retry canceled tasks.

## Timeouts

Tool execution is wrapped in `asyncio.wait_for(...)` using:

- `Task.timeout_s` (persisted on the task)

In the current orchestrator validation code, task defaults come from the tool manifest
(`tool.manifest.default_timeout_s`) when a planned/reflex task does not specify `timeout_s`.

`REFLEXOR_EXECUTOR_DEFAULT_TIMEOUT_S` is currently a configuration guardrail (settings validation
ensures the executor visibility timeout is >= this value); the executor itself uses the
per-task `timeout_s` stored on the task.

Timeout outcomes produce `ToolResult.error_code="TIMEOUT"`.

## Retries and backoff

Retry behavior is deterministic and driven by:

- `Task.max_attempts` (stored on each task)
- retry settings:
  - backoff parameters wired from settings (`REFLEXOR_EXECUTOR_RETRY_BASE_DELAY_S`,
    `REFLEXOR_EXECUTOR_RETRY_MAX_DELAY_S`, `REFLEXOR_EXECUTOR_RETRY_JITTER`)
  - retryable error-code / HTTP-status classification defaults defined in code (`RetryPolicy`)

Classification (`ErrorClassifier`):

- `ToolResult.error_code="APPROVAL_REQUIRED"` → **no retry** (`waiting_approval`)
- Default transient error codes: `TIMEOUT`, `TOOL_ERROR`
- HTTP status in `ToolResult.data`/`debug` (e.g., `status_code=503`) can also classify as transient

Scheduling:

- Transient failures with attempts remaining are `nack(...)`ed with an exponential backoff delay.
- Permanent failures are `ack(...)`ed (no retry).
- If `task.attempts >= task.max_attempts`, the lease is acked and the executor reports
  `MAX_ATTEMPTS_EXHAUSTED`.

Backoff:

- Delay is computed as `base_delay_s * 2^(attempt-1)` and capped at `max_delay_s`.
- The current executor scheduling path uses a pure exponential backoff (no jitter).
  (`RetryPolicy.jitter` exists but is not applied by `ExecutorService` today.)

Settings:

- `REFLEXOR_EXECUTOR_RETRY_BASE_DELAY_S`
- `REFLEXOR_EXECUTOR_RETRY_MAX_DELAY_S`
- `REFLEXOR_EXECUTOR_RETRY_JITTER`

## Idempotency ledger (dedupe)

If a tool is marked `idempotent=true` in its manifest, the executor records outcomes under
`ToolCall.idempotency_key` in a durable idempotency ledger.

Cache hits:

- Before executing an idempotent tool call, the executor checks the ledger for a **successful**
  cached outcome under the same `idempotency_key`.
- If found, the task is marked `succeeded` without running the tool.
- The run packet records the execution disposition as `cached`.

Important caveat (dedupe surprises):

- The cache lookup is keyed by **`idempotency_key` + tool name** only; it does not compare args.
  If two semantically-different operations share a key, the second one will reuse the first result.

Practical guidance:

- Treat the idempotency key as part of the tool-call contract.
- Ensure key generation incorporates all inputs that affect side effects (use canonical JSON + a
  stable hash, as is done elsewhere in the codebase).

## Approvals (human-in-the-loop gating)

Approvals are created by the policy enforcement boundary:

- Policy requires approval → tool is not executed
- A pending approval record is created with:
  - `payload_hash`: stable SHA-256 over canonical JSON of **redacted** args
  - `preview`: sanitized, bounded human-readable summary

`create_pending(...)` is idempotent by `tool_call_id`:

- multiple evaluations for the same tool call return the same approval (no duplicates)

When an approval is `approved`, a subsequent evaluation of the same tool call allows the tool to
execute (but the system currently requires re-queueing as described above).

## Run packet audit trail

Each execution attempt appends a sanitized record to the run packet:

- tool identifiers: `task_id`, `tool_call_id`, `tool_name`
- `status`: one of `cached`, `succeeded`, `failed_transient`, `failed_permanent`,
  `waiting_approval`, `denied`, `canceled`
- `error_code` (when failed)
- retry metadata: `will_retry`, `retry_after_s`, `attempt`, `max_attempts`
- policy decision summary: `action`, `reason_code`, `rule_id`
- `result_summary`: sanitized + truncated `ToolResult`
- approval metadata: `approval_id`, `approval_status`

Run packets are sanitized before persistence to avoid leaking secrets and large blobs.

## Running locally (dry-run)

Reflexor is currently easiest to run locally via in-process wiring (tests provide the most
up-to-date examples).

Safe defaults:

- `REFLEXOR_PROFILE=dev`
- `REFLEXOR_DRY_RUN=true`
- keep `REFLEXOR_ENABLED_SCOPES` minimal

Reference wiring examples:

- Basic worker loop: `tests/integration/test_worker_runner.py`
- Retry + delayed nack: `tests/integration/test_worker_retry_flow.py`
- Idempotency cache dedupe: `tests/integration/test_worker_idempotency_dedupe.py`
- Approval gating: `tests/integration/test_worker_approval_block.py`

## Troubleshooting

### Task stuck in `waiting_approval`

Expected behavior:

- the worker acks the lease and stops retrying
- the task remains blocked until an approval decision is recorded

Checks:

- Is there an `Approval` row for the `tool_call_id`?
- Is the approval still `pending`?
- Has the task been moved back to `queued` and re-enqueued after approval?

### Repeated failures / retry storms

Checks:

- `Task.attempts` vs `Task.max_attempts`
- `ToolResult.error_code` classification (`TIMEOUT` / `TOOL_ERROR` transient by default)
- queue visibility timeout too small (lease expiry causing duplicate deliveries)
- worker crash path (exceptions cause immediate `nack(..., delay_s=0)` with `reason=worker_exception`)

### Unexpected `cached` results (idempotency collisions)

Likely cause:

- multiple tasks sharing the same `idempotency_key` unintentionally

Fix:

- ensure the key is derived from the true side-effecting payload/inputs (stable hash of canonical
  JSON is recommended) and is unique per semantic operation

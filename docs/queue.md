# Queue

Reflexor defines a small, backend-agnostic queue interface for delivering `TaskEnvelope` messages to
workers with **at-least-once** semantics. Concrete implementations live in infrastructure.

Code:

- Interface + contracts: `src/reflexor/orchestrator/queue/`
- In-memory backend: `src/reflexor/infra/queue/in_memory_queue/core.py`
- Redis Streams backend: `src/reflexor/infra/queue/redis_streams/core.py`
- Settings wiring: `src/reflexor/infra/queue/factory.py`

## Core models

### `TaskEnvelope`

Defined in `src/reflexor/orchestrator/queue/task_envelope.py`.

Fields (high-level):

- `envelope_id` (UUID4 string): stable message identity.
- `task_id`, `run_id` (UUID4 strings): domain-level identifiers for orchestration.
- `attempt` (int): delivery attempt counter (starts at `0`).
- `created_at_ms`, `available_at_ms` (ms since epoch): creation time + earliest eligible delivery.
- `priority` (optional int): advisory priority (backend-dependent).
- `correlation_ids` (optional dict): caller-provided correlation IDs (e.g. `event_id`).
- `payload` / `trace` (optional dict): reserved for future extensions.

### `Lease`

Defined in `src/reflexor/orchestrator/queue/interface.py`.

A `Lease` represents a reserved delivery of an envelope:

- `lease_id` (string)
  - in-memory: UUID4 string
  - Redis Streams: stream entry ID like `"1700000000000-0"`
- `envelope` (`TaskEnvelope`)
- `leased_at_ms` (ms since epoch)
- `visibility_timeout_s` (seconds)
- `attempt` (int) which must mirror `lease.envelope.attempt`

## Queue interface

Defined in `src/reflexor/orchestrator/queue/interface.py`.

Methods:

- `enqueue(envelope)`: submit a task envelope for delivery.
- `dequeue(timeout_s=None, *, wait_s=0.0) -> Lease | None`: reserve the next available envelope.
- `ack(lease)`: confirm successful processing; removes the envelope.
- `nack(lease, delay_s=None, reason=None)`: release the lease back to the queue (optionally delay).
- `aclose()`: graceful shutdown.

### `dequeue(...)` parameters

- `timeout_s`: **visibility timeout** for the returned lease.
  - If omitted, the backend default is used (see `REFLEXOR_QUEUE_VISIBILITY_TIMEOUT_S`).
- `wait_s`: long-poll behavior
  - `0` (default): non-blocking; return `None` if nothing is currently available.
  - `> 0`: wait up to `wait_s` seconds for an envelope to become available.
  - `None`: wait indefinitely until an envelope is available (or the queue is closed).

### Closure behavior (`QueueClosed`)

`QueueClosed` is defined in `src/reflexor/orchestrator/queue/errors.py`.

Backends are expected to raise `QueueClosed` for operations after `aclose()`:

- `enqueue`, `dequeue`, `ack`, and `nack` raise `QueueClosed`.
- a pending `dequeue(wait_s=...)` is unblocked when the queue closes and raises `QueueClosed`.

## Semantics

The invariants are documented in `src/reflexor/orchestrator/queue/semantics.py`. In summary:

- **At-least-once delivery**: an envelope may be delivered more than once.
- **`ack` removes**: once acked, an envelope must not be delivered again.
- **`nack` requeues**: optionally delayed.
- **Visibility timeout** may cause redelivery (lease expiry).
- **Best-effort ordering** only; do not assume strict FIFO.
- **Attempts are monotonic**: each successful `dequeue` increments `TaskEnvelope.attempt`.

### Delayed scheduling

Two mechanisms can delay eligibility:

1) `TaskEnvelope.available_at_ms`
2) `nack(..., delay_s=...)` (which updates `available_at_ms` for the requeued envelope)

Backends must not deliver an envelope before it is due.

### Visibility timeout and redelivery

If a lease is not acked/nacked before its visibility timeout expires, the envelope becomes eligible
again (best-effort). Attempt counters increment on each redelivery.

In the in-memory backend, `ack`/`nack` of an **expired** lease is ignored (no crash); the envelope
may already have been redelivered under a new lease.

## Observability hooks

The queue layer exposes a lightweight observer interface for metrics/logging without coupling to a
specific telemetry stack.

- Observer contracts: `src/reflexor/orchestrator/queue/observer.py`
- In-memory backend integration: `src/reflexor/infra/queue/in_memory_queue/core.py`

Callbacks:

- `on_enqueue`
- `on_dequeue`
- `on_ack`
- `on_nack`
- `on_redeliver` (visibility-timeout redelivery)

Each callback receives a small observation object containing `correlation_ids` and timing fields.
If no observer is provided, a `NoopQueueObserver` is used.

## Settings and factory wiring

Settings:

- `REFLEXOR_QUEUE_BACKEND` (default `inmemory`)
- `REFLEXOR_QUEUE_VISIBILITY_TIMEOUT_S` (default `60`)

Factory:

- `reflexor.infra.queue.factory.build_queue(settings) -> Queue`

The factory is DI-friendly (no globals) and returns the narrow `Queue` interface.

## Redis Streams backend (notes + limitations)

The `redis_streams` backend implements the same `Queue` interface using Redis Streams + consumer
groups.

Key settings (see `docs/configuration.md` for the full list):

- `REFLEXOR_QUEUE_BACKEND=redis_streams`
- `REFLEXOR_REDIS_URL=redis://...`
- `REFLEXOR_REDIS_STREAM_KEY` / `REFLEXOR_REDIS_CONSUMER_GROUP` / `REFLEXOR_REDIS_CONSUMER_NAME`
- `REFLEXOR_REDIS_DELAYED_ZSET_KEY` (for delayed scheduling)
- `REFLEXOR_REDIS_VISIBILITY_TIMEOUT_MS` (minimum idle threshold for redelivery)
- `REFLEXOR_REDIS_STREAM_MAXLEN` (optional approximate trimming)

Semantics:

- **Group initialization:** `ensure_ready()` creates the stream + group (`XGROUP CREATE ... MKSTREAM`)
  and is called at API startup (`AppContainer.start()`).
- **Dequeue new work:** `XREADGROUP` with `>` reads new messages for the group.
- **Redelivery:** before blocking on new work, `dequeue()` attempts to reclaim idle pending messages:
  - uses `XAUTOCLAIM` when available (Redis 6.2+), otherwise falls back to a bounded
    `XPENDING` + `XCLAIM` scan.
- **Delayed scheduling:** future envelopes are stored as canonical JSON in a ZSET and promoted to the
  stream using a Lua script (atomic best-effort: `ZRANGEBYSCORE` → `ZREM` → `XADD`).
- **Nack behavior:** `nack(...)` acks the current stream entry and re-enqueues a *new* envelope
  (immediate or delayed), with a deterministic attempt increment (`attempt = previous_attempt + 1`).

Limitations / operational notes:

- **Stream growth:** `XACK` removes the message from the pending set but does not delete the stream
  entry. Use `REFLEXOR_REDIS_STREAM_MAXLEN` if you need bounded stream size.
- **Fallback redelivery scan:** the `XPENDING` + `XCLAIM` fallback is bounded and may miss eligible
  messages when there are many pending entries. Prefer Redis 6.2+ so `XAUTOCLAIM` is available.
- **Consumer identity:** for multi-process deployments, set a unique
  `REFLEXOR_REDIS_CONSUMER_NAME` per worker process for clean redelivery behavior and observability.

## Adding a backend (guide)

When adding an additional backend (Taskiq/SAQ/SQS/etc), implement the `Queue` Protocol and
preserve the invariants above.

Recommended approach:

1) Keep `TaskEnvelope` JSON as the on-wire payload.
2) Map `Lease` to your backend reservation/visibility mechanism:
   - Redis Streams: consumer group pending entries + idle-time reprocessing.
   - Redis Lists/Sets: use an in-flight structure with expiry timestamps.
3) Implement delayed scheduling:
   - Use a sorted set keyed by `available_at_ms` and promote due items to a ready structure.
4) Ensure `aclose()`:
   - stops any background tasks/threads
   - unblocks pending consumers
   - prevents further operations (raise `QueueClosed`)

Do not strengthen guarantees (e.g., strict FIFO) unless you can preserve them across all backends.

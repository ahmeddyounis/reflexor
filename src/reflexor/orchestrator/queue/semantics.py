"""Queue semantics and invariants.

This module documents the baseline behavior required of `reflexor.orchestrator.queue.Queue`
implementations. Backends should treat these as **invariants** so swapping implementations does not
change application behavior.

Invariants:

1) At-least-once delivery
   - A leased message may be re-delivered (e.g., if the consumer crashes or fails to ack).
   - Consumers must treat processing as potentially duplicated and use idempotency.

2) `ack(lease)` removes the message
   - Once acked, the envelope must not be delivered again.

3) `nack(lease, delay_s=...)` requeues the message
   - Nacking releases the lease and makes the envelope eligible again.
   - If `delay_s` is provided, the envelope must not be eligible until the delay elapses.

4) Visibility timeout may cause re-delivery
   - If a lease is not acked/nacked before its visibility timeout elapses, the envelope may become
     eligible for delivery again.

5) Best-effort ordering
   - Ordering is not a strict FIFO guarantee under concurrency, retries, or multiple consumers.
   - Consumers must not rely on delivery order for correctness.

6) `dequeue(...)` respects delayed scheduling
   - An envelope must not be delivered before `TaskEnvelope.available_at_ms`.
"""

from __future__ import annotations

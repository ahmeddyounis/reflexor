"""Queue abstraction used by orchestrators.

This package defines a backend-agnostic queue interface. Concrete implementations live in
infrastructure (e.g., `reflexor.infra.queue.*`) so they can be swapped without changing
application/orchestrator code.

Clean Architecture constraints:
- Domain must not import `reflexor.orchestrator.queue`.
- API/worker/CLI should depend on the queue *interface* only (this package), not on specific
  backends.
- Backends may depend on this package, but this package must never import backends.
"""

from __future__ import annotations

from reflexor.orchestrator.queue.contracts import QueueBackend, QueueMessage
from reflexor.orchestrator.queue.interface import Lease, Queue
from reflexor.orchestrator.queue.task_envelope import TaskEnvelope

__all__ = ["Lease", "Queue", "QueueBackend", "QueueMessage", "TaskEnvelope"]

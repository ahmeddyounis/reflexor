"""Executor subsystem (application layer).

The executor is responsible for taking queued work (tasks/tool calls) and driving execution
through:
- idempotency checks
- policy enforcement
- tool invocation
- persistence updates

Clean Architecture boundaries:
- Allowed dependencies: `reflexor.domain`, `reflexor.storage` ports/UoW, queue interface contracts,
  tool boundary types/registries, and the policy enforcement boundary.
- Forbidden dependencies: FastAPI/Starlette and CLI entrypoints.

Process/runtime concerns (signals, long-running loops, process lifecycle) belong in
`reflexor.worker`.
"""

from __future__ import annotations

__all__ = ["concurrency", "errors", "idempotency", "retries", "service", "state"]

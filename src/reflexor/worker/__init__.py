"""Worker runtime (process boundary).

The worker package owns long-running runtime concerns: process lifecycle, signal handling, and
coordinating the executor loop.

Clean Architecture boundaries:
- Worker is an outer-layer runtime/interface package.
- It may depend on the executor application layer and on infrastructure wiring for adapters.
- It should not import FastAPI/Starlette or CLI entrypoints.
"""

from __future__ import annotations

__all__ = ["runner", "signals"]

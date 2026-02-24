"""Orchestrator layer (application-facing coordination).

This package is intended to host workflow orchestration primitives that coordinate domain models,
tools, and policy. It should remain framework-agnostic and backend-agnostic.

Clean Architecture constraints:
- `reflexor.domain` must not import anything in `reflexor.orchestrator`.
- Orchestrator may depend on:
  - `reflexor.domain`
  - `reflexor.config`
  - queue interface/contracts (`reflexor.orchestrator.queue`)
  - tool boundary types/registries (`reflexor.tools.*`)
- Orchestrator must not import outer-layer frameworks or entrypoints (FastAPI, SQLAlchemy, httpx,
  worker processes, API modules, CLI modules, etc.).
"""

from __future__ import annotations

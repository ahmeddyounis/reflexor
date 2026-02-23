"""Orchestrator layer (application-facing coordination).

This package is intended to host workflow orchestration primitives that coordinate domain models,
tools, and policy. It should remain framework-agnostic and backend-agnostic.

Clean Architecture constraints:
- `reflexor.domain` must not import anything in `reflexor.orchestrator`.
- Infrastructure (API/worker/CLI) may depend on orchestrator interfaces, but should not depend on
  concrete backend implementations directly.
"""

from __future__ import annotations

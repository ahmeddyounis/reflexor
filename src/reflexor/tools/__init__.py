"""Tooling boundaries (Clean Architecture).

This package is split into:

- `reflexor.tools.sdk`: boundary types and interfaces ("ports")
- `reflexor.tools.impl`: concrete tool implementations ("adapters")

Dependency rules:

- Tools may depend on `reflexor.domain` and on `reflexor.config` / `reflexor.security` /
  `reflexor.observability` utilities.
- The domain layer must never import `reflexor.tools.*`.
"""

from __future__ import annotations

"""Security policy subsystem (Clean Architecture boundary).

This package is responsible for policy decisions around tool execution (allow/deny, approval
requirements, scope checks, etc.).

Dependency rules:
- Allowed: `reflexor.domain`, `reflexor.config`, and `reflexor.security.*` utilities.
- Forbidden: frameworks or outer layers such as FastAPI/Starlette, SQLAlchemy, queues/workers,
  CLIs, or infrastructure adapters.

Modules:
- `decision`: policy decision/result types
- `rules`: composable rule interfaces and built-in rules
- `gate`: main evaluation entrypoint
- `context`: policy inputs (settings/tool specs)
- `approvals`: HITL/approval integration points (placeholder)
- `enforcement`: helpers to apply decisions at runtime (placeholder)
"""

from __future__ import annotations

__all__ = [
    "approvals",
    "context",
    "decision",
    "enforcement",
    "gate",
    "rules",
]

"""Concrete tool implementations (infrastructure).

Implementations live here (or in `reflexor.infra.*` for larger adapters). They may depend on:

- `reflexor.tools.sdk` (interfaces)
- `reflexor.domain` (models/enums)
- `reflexor.config` / `reflexor.security` / `reflexor.observability` (utilities)

They must not be imported by `reflexor.domain`.
"""

from __future__ import annotations

from reflexor.tools.impl.echo import EchoTool

__all__ = ["EchoTool"]

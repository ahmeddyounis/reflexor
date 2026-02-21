from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from reflexor.domain.models import ToolCall

ToolOutput = Mapping[str, object]


class Tool(Protocol):
    """Boundary interface for tool implementations.

    Implementations may perform side effects, but must not do so at import time.
    """

    name: str

    def execute(self, call: ToolCall) -> ToolOutput:
        """Execute a validated tool call and return a JSON-serializable output."""

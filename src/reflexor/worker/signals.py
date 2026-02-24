"""Signal handling helpers for worker processes."""

from __future__ import annotations

from dataclasses import dataclass
from signal import Signals


@dataclass(frozen=True, slots=True)
class ShutdownSignal:
    """Represents a received shutdown signal."""

    signal: Signals

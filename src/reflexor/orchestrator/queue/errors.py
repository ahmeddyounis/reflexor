from __future__ import annotations


class QueueClosed(RuntimeError):
    """Raised when a queue operation is attempted after the queue is closed."""


__all__ = ["QueueClosed"]

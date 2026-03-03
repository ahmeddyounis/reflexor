"""CLI commands.

Each command module should register itself with the Typer app without importing
infrastructure/ORM code. Command handlers should call application services or a client
abstraction provided by the CLI container.
"""

from __future__ import annotations

__all__ = []

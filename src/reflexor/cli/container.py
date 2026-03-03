"""CLI composition root.

Clean Architecture:
- The CLI is an outer interface layer (like the API). It should remain thin.
- Command handlers must not access the ORM directly; they should call application services
  or a client abstraction.
- This module wires CLI dependencies without importing concrete ORM/DB adapters.
"""

from __future__ import annotations

from dataclasses import dataclass

from reflexor.config import ReflexorSettings, get_settings


@dataclass(slots=True)
class CliContainer:
    """Dependencies used by CLI commands."""

    settings: ReflexorSettings

    @classmethod
    def build(cls, *, settings: ReflexorSettings | None = None) -> CliContainer:
        effective_settings = get_settings() if settings is None else settings
        return cls(settings=effective_settings)


__all__ = ["CliContainer"]


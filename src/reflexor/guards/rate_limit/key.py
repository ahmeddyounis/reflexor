from __future__ import annotations

from dataclasses import dataclass


def _normalize_optional_str(value: str | None, *, field_name: str) -> str | None:
    if value is None:
        return None
    trimmed = str(value).strip()
    if not trimmed:
        raise ValueError(f"{field_name} must be non-empty when provided")
    return trimmed


@dataclass(frozen=True, slots=True)
class RateLimitKey:
    """Key for rate-limiting decisions (hashable, DI-friendly)."""

    scope: str | None = None
    tool_name: str | None = None
    destination: str | None = None
    run_id: str | None = None

    def __post_init__(self) -> None:
        scope = _normalize_optional_str(self.scope, field_name="scope")
        tool_name = _normalize_optional_str(self.tool_name, field_name="tool_name")
        destination = _normalize_optional_str(self.destination, field_name="destination")
        run_id = _normalize_optional_str(self.run_id, field_name="run_id")

        if scope is None and tool_name is None and destination is None and run_id is None:
            raise ValueError("at least one of scope/tool_name/destination/run_id must be set")

        object.__setattr__(self, "scope", scope)
        object.__setattr__(self, "tool_name", tool_name)
        object.__setattr__(self, "destination", destination)
        object.__setattr__(self, "run_id", run_id)


__all__ = ["RateLimitKey"]

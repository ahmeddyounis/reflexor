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
class CircuitBreakerKey:
    """Key for circuit breaker decisions (hashable, DI-friendly)."""

    tool_name: str | None = None
    destination: str | None = None
    scope: str | None = None
    signature: str | None = None

    def __post_init__(self) -> None:
        tool_name = _normalize_optional_str(self.tool_name, field_name="tool_name")
        destination = _normalize_optional_str(self.destination, field_name="destination")
        scope = _normalize_optional_str(self.scope, field_name="scope")
        signature = _normalize_optional_str(self.signature, field_name="signature")

        if tool_name is None and destination is None and scope is None and signature is None:
            raise ValueError("at least one of tool_name/destination/scope/signature must be set")

        object.__setattr__(self, "tool_name", tool_name)
        object.__setattr__(self, "destination", destination)
        object.__setattr__(self, "scope", scope)
        object.__setattr__(self, "signature", signature)


__all__ = ["CircuitBreakerKey"]

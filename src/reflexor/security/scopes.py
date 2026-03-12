from __future__ import annotations

from enum import StrEnum


class Scope(StrEnum):
    """Canonical permission scopes for Reflexor.

    Scope values are stable, dot-delimited strings intended for configuration and persistence.
    """

    NET_HTTP = "net.http"
    FS_READ = "fs.read"
    FS_WRITE = "fs.write"
    WEBHOOK_EMIT = "webhook.emit"


ALL_SCOPES: frozenset[str] = frozenset(scope.value for scope in Scope)


def validate_scopes(scopes: list[str]) -> list[str]:
    """Normalize, dedupe, and raise if any unknown scope is present."""

    normalized: list[str] = []
    unknown: list[str] = []
    seen: set[str] = set()

    for raw_scope in scopes:
        scope = raw_scope.strip()
        if not scope:
            raise ValueError("scope entries must be non-empty")
        if scope not in ALL_SCOPES:
            unknown.append(scope)
            continue
        if scope in seen:
            continue
        seen.add(scope)
        normalized.append(scope)

    if not unknown:
        return normalized

    known = ", ".join(sorted(ALL_SCOPES))
    raise ValueError(f"unknown scope(s): {unknown!r} (known: {known})")

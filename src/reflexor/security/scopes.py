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
    """Raise if any unknown scope is present."""

    unknown = [scope for scope in scopes if scope not in ALL_SCOPES]
    if not unknown:
        return scopes

    known = ", ".join(sorted(ALL_SCOPES))
    raise ValueError(f"unknown scope(s): {unknown!r} (known: {known})")

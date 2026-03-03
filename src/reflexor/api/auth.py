"""Authentication dependencies for the API layer.

This module currently implements a lightweight admin API key check suitable for an MVP.

Rules:
- If `REFLEXOR_ADMIN_API_KEY` is unset:
  - allow admin access in `dev`
  - deny admin access in `prod`
- If set, require the `X-API-Key` header to match using a constant-time compare.
"""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, status

from reflexor.api.deps import ContainerDep


def _is_valid_api_key(provided: str | None, expected: str) -> bool:
    return hmac.compare_digest(provided or "", expected)


async def require_admin(
    container: ContainerDep,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> None:
    expected = container.settings.admin_api_key

    if expected is None:
        if container.settings.profile == "dev":
            return
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="admin api key is required",
        )

    if not _is_valid_api_key(x_api_key, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid api key",
        )


async def require_events_access(
    container: ContainerDep,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> None:
    if not container.settings.events_require_admin:
        return
    await require_admin(container=container, x_api_key=x_api_key)


__all__ = ["require_admin", "require_events_access"]

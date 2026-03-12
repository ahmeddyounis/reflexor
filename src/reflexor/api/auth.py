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

_AUTH_CHALLENGE = 'Bearer realm="reflexor-admin"'
_X_API_KEY_HEADER = Header(default=None, alias="X-API-Key")
_AUTHORIZATION_HEADER = Header(default=None, alias="Authorization")


def _is_valid_api_key(provided: str | None, expected: str) -> bool:
    return hmac.compare_digest(provided or "", expected)


def _auth_error(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": _AUTH_CHALLENGE},
    )


def _normalize_header_values(values: list[str] | None) -> tuple[str, ...]:
    if values is None:
        return ()
    normalized = tuple(value.strip() for value in values if value.strip())
    if len(set(normalized)) > 1:
        raise _auth_error("conflicting authentication headers")
    return normalized


def _extract_bearer_token(authorization_values: tuple[str, ...]) -> str | None:
    bearer_tokens: list[str] = []
    for value in authorization_values:
        scheme, _, token = value.partition(" ")
        if scheme.lower() != "bearer":
            continue
        normalized_token = token.strip()
        if normalized_token:
            bearer_tokens.append(normalized_token)

    if len(set(bearer_tokens)) > 1:
        raise _auth_error("conflicting authentication headers")
    if not bearer_tokens:
        return None
    return bearer_tokens[0]


def _resolve_provided_api_key(
    *,
    x_api_key_values: list[str] | None,
    authorization_values: list[str] | None,
) -> str | None:
    normalized_api_keys = _normalize_header_values(x_api_key_values)
    normalized_authorization = _normalize_header_values(authorization_values)

    api_key = normalized_api_keys[0] if normalized_api_keys else None
    bearer_token = _extract_bearer_token(normalized_authorization)

    if api_key is not None and bearer_token is not None and api_key != bearer_token:
        raise _auth_error("conflicting authentication headers")

    if bearer_token is not None:
        return bearer_token
    return api_key


async def require_admin(
    container: ContainerDep,
    x_api_key: list[str] | None = _X_API_KEY_HEADER,
    authorization: list[str] | None = _AUTHORIZATION_HEADER,
) -> None:
    expected = container.settings.admin_api_key

    if expected is None:
        if container.settings.profile == "dev":
            return
        raise _auth_error("admin credentials are required")

    provided = _resolve_provided_api_key(
        x_api_key_values=x_api_key,
        authorization_values=authorization,
    )
    if not _is_valid_api_key(provided, expected):
        raise _auth_error("invalid admin credentials")


async def require_events_access(
    container: ContainerDep,
    x_api_key: list[str] | None = _X_API_KEY_HEADER,
    authorization: list[str] | None = _AUTHORIZATION_HEADER,
) -> None:
    if not container.settings.events_require_admin:
        return
    await require_admin(container=container, x_api_key=x_api_key, authorization=authorization)


__all__ = ["require_admin", "require_events_access"]

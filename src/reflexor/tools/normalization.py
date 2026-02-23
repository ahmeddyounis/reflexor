from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from reflexor.security.fs_safety import resolve_path_in_workspace
from reflexor.security.net_safety import validate_and_normalize_url

ModelT = TypeVar("ModelT", bound=BaseModel)


def normalize_path_in_workspace(path: Path, *, workspace_root: Path) -> Path:
    """Normalize a path relative to `workspace_root`, rejecting escapes.

    - Relative paths are resolved under `workspace_root`
    - `..` segments are collapsed via `resolve(strict=False)`
    - The final path must stay within `workspace_root`
    """

    return resolve_path_in_workspace(path, workspace_root=workspace_root, must_exist=False)


def normalize_http_url(value: str) -> str:
    """Normalize and validate an HTTPS URL for consistency.

    This helper:
    - strips surrounding whitespace
    - lowercases scheme and hostname
    - rejects embedded credentials
    - enforces https and basic SSRF guardrails (e.g., blocks IP literals)
    """

    return validate_and_normalize_url(value, require_https=True)


def normalize_tool_args(args: ModelT, *, workspace_root: Path) -> ModelT:
    """Apply conservative, generic normalization to tool args.

    Normalization is type-driven:
    - `Path` values are resolved into `workspace_root` and rejected if they escape.
    - `str` values in fields containing 'url' are normalized as http(s) URLs.
    """

    updates: dict[str, object] = {}
    for field_name in type(args).model_fields:
        value = getattr(args, field_name)
        normalized = _normalize_value(
            field_name=field_name, value=value, workspace_root=workspace_root
        )
        if normalized is not value:
            updates[field_name] = normalized

    if not updates:
        return args

    return args.model_copy(update=updates)


def _normalize_value(*, field_name: str, value: object, workspace_root: Path) -> object:
    if isinstance(value, Path):
        return normalize_path_in_workspace(value, workspace_root=workspace_root)

    if isinstance(value, BaseModel):
        return normalize_tool_args(value, workspace_root=workspace_root)

    if isinstance(value, str) and "url" in field_name.lower():
        return normalize_http_url(value)

    if isinstance(value, list):
        return _normalize_iterable(
            field_name=field_name, value=value, workspace_root=workspace_root
        )

    if isinstance(value, tuple):
        normalized = _normalize_iterable(
            field_name=field_name, value=value, workspace_root=workspace_root
        )
        if normalized is value:
            return value
        return tuple(normalized)

    return value


def _normalize_iterable(
    *, field_name: str, value: Iterable[object], workspace_root: Path
) -> list[object] | Iterable[object]:
    normalized_items: list[object] = []
    changed = False
    for item in value:
        normalized = _normalize_value(
            field_name=field_name, value=item, workspace_root=workspace_root
        )
        if normalized is not item:
            changed = True
        normalized_items.append(normalized)

    if not changed and isinstance(value, list):
        return value
    return normalized_items

"""Deprecated shim for `reflexor.security.fs_safety`.

Policy code must not depend on `reflexor.tools.*`; this module remains as a thin re-export for
internal churn reduction.
"""

from __future__ import annotations

from reflexor.security.fs_safety import (
    atomic_write_bytes,
    atomic_write_text,
    read_bytes_limited,
    read_text_limited,
    resolve_path_in_workspace,
)

__all__ = [
    "atomic_write_bytes",
    "atomic_write_text",
    "read_bytes_limited",
    "read_text_limited",
    "resolve_path_in_workspace",
]

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def resolve_path_in_workspace(
    path: Path,
    *,
    workspace_root: Path,
    must_exist: bool = False,
) -> Path:
    """Resolve `path` into `workspace_root`, preventing traversal and symlink escapes.

    - Relative paths are resolved under `workspace_root`
    - The result is a realpath-like resolution (symlinks collapsed where possible)
    - The resolved path must stay within `workspace_root`
    """

    base = _normalize_workspace_root(workspace_root)

    expanded = path.expanduser()
    candidate = expanded if expanded.is_absolute() else (base / expanded)
    resolved = candidate.resolve(strict=must_exist)

    if not resolved.is_relative_to(base):
        raise ValueError(f"path escapes workspace root: {path!r}")

    return resolved


def read_bytes_limited(
    path: Path,
    *,
    workspace_root: Path,
    max_bytes: int,
) -> bytes:
    """Read a file within the workspace, enforcing a maximum size."""

    if max_bytes < 0:
        raise ValueError("max_bytes must be >= 0")

    resolved = resolve_path_in_workspace(path, workspace_root=workspace_root, must_exist=True)
    size = resolved.stat().st_size
    if size > max_bytes:
        raise ValueError(f"file exceeds max_bytes={max_bytes}: {resolved}")
    return resolved.read_bytes()


def read_text_limited(
    path: Path,
    *,
    workspace_root: Path,
    max_bytes: int,
    encoding: str = "utf-8",
    errors: str = "strict",
) -> str:
    """Read a text file within the workspace, enforcing a maximum size."""

    data = read_bytes_limited(path, workspace_root=workspace_root, max_bytes=max_bytes)
    return data.decode(encoding, errors=errors)


def atomic_write_bytes(
    path: Path,
    data: bytes,
    *,
    workspace_root: Path,
    max_bytes: int | None = None,
    create_parents: bool = True,
    mode: int | None = None,
) -> Path:
    """Atomically write bytes within the workspace (temp file + replace)."""

    if max_bytes is not None and max_bytes < 0:
        raise ValueError("max_bytes must be >= 0")
    if max_bytes is not None and len(data) > max_bytes:
        raise ValueError(f"data exceeds max_bytes={max_bytes}")

    resolved = resolve_path_in_workspace(path, workspace_root=workspace_root, must_exist=False)
    if create_parents:
        resolved.parent.mkdir(parents=True, exist_ok=True)

    tmp_path: Path | None = None
    fd: int | None = None
    try:
        fd, tmp_name = tempfile.mkstemp(
            dir=str(resolved.parent),
            prefix=f".{resolved.name}.",
            suffix=".tmp",
        )
        tmp_path = Path(tmp_name)
        with os.fdopen(fd, "wb") as handle:
            fd = None
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())

        if mode is not None:
            tmp_path.chmod(mode)

        os.replace(tmp_path, resolved)
        tmp_path = None
        return resolved
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass


def atomic_write_text(
    path: Path,
    text: str,
    *,
    workspace_root: Path,
    max_bytes: int | None = None,
    encoding: str = "utf-8",
    errors: str = "strict",
    create_parents: bool = True,
    mode: int | None = None,
) -> Path:
    """Atomically write text within the workspace (temp file + replace)."""

    data = text.encode(encoding, errors=errors)
    return atomic_write_bytes(
        path,
        data,
        workspace_root=workspace_root,
        max_bytes=max_bytes,
        create_parents=create_parents,
        mode=mode,
    )


def _normalize_workspace_root(workspace_root: Path) -> Path:
    expanded = workspace_root.expanduser()
    if not expanded.is_absolute():
        raise ValueError("workspace_root must be an absolute path")
    return expanded.resolve(strict=False)

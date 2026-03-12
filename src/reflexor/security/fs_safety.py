from __future__ import annotations

import contextlib
import os
import stat
from pathlib import Path
from uuid import uuid4

_DEFAULT_NEW_FILE_MODE = 0o600


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
    resolved = _resolve_candidate_path(candidate, original_path=path, must_exist=must_exist)

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
    with resolved.open("rb") as handle:
        data = handle.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError(f"file exceeds max_bytes={max_bytes}: {resolved}")
    return data


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
    parent = _prepare_atomic_write_parent(
        resolved.parent,
        workspace_root=workspace_root,
        create_parents=create_parents,
    )

    parent_fd: int | None = None
    tmp_name: str | None = None
    fd: int | None = None
    try:
        parent_fd = _open_directory_fd(parent)
        target_mode = _resolve_target_mode(parent_fd, resolved.name, explicit_mode=mode)

        tmp_name = _atomic_temp_name(resolved.name)
        fd = os.open(
            tmp_name,
            _temp_open_flags(),
            dir_fd=parent_fd,
            mode=_DEFAULT_NEW_FILE_MODE,
        )
        with os.fdopen(fd, "wb") as handle:
            fd = None
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), target_mode)

        os.replace(tmp_name, resolved.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        _fsync_directory(parent_fd)
        tmp_name = None
        return resolved
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if tmp_name is not None and parent_fd is not None:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name, dir_fd=parent_fd)
        if parent_fd is not None:
            with contextlib.suppress(OSError):
                os.close(parent_fd)


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


def _resolve_candidate_path(candidate: Path, *, original_path: Path, must_exist: bool) -> Path:
    if must_exist:
        try:
            return candidate.resolve(strict=True)
        except FileNotFoundError:
            raise
        except (OSError, RuntimeError) as exc:
            raise ValueError(f"failed to resolve path within workspace: {original_path!r}") from exc

    existing_ancestor = _closest_existing_path(candidate)
    if existing_ancestor is None:
        return candidate

    try:
        resolved_ancestor = existing_ancestor.resolve(strict=True)
    except (FileNotFoundError, OSError, RuntimeError) as exc:
        raise ValueError(f"failed to resolve path within workspace: {original_path!r}") from exc

    suffix = candidate.relative_to(existing_ancestor)
    return resolved_ancestor / suffix


def _closest_existing_path(path: Path) -> Path | None:
    current = path
    while True:
        try:
            current.lstat()
            return current
        except FileNotFoundError:
            parent = current.parent
            if parent == current:
                return None
            current = parent


def _prepare_atomic_write_parent(
    parent: Path,
    *,
    workspace_root: Path,
    create_parents: bool,
) -> Path:
    if create_parents:
        parent.mkdir(parents=True, exist_ok=True)
    return resolve_path_in_workspace(parent, workspace_root=workspace_root, must_exist=True)


def _open_directory_fd(path: Path) -> int:
    flags = os.O_RDONLY
    flags |= getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    return os.open(path, flags)


def _temp_open_flags() -> int:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    return flags


def _atomic_temp_name(target_name: str) -> str:
    return f".{target_name}.{uuid4().hex}.tmp"


def _resolve_target_mode(parent_fd: int, target_name: str, *, explicit_mode: int | None) -> int:
    if explicit_mode is not None:
        return explicit_mode

    try:
        current = os.stat(target_name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return _DEFAULT_NEW_FILE_MODE

    return stat.S_IMODE(current.st_mode) or _DEFAULT_NEW_FILE_MODE


def _fsync_directory(parent_fd: int) -> None:
    with contextlib.suppress(OSError):
        os.fsync(parent_fd)

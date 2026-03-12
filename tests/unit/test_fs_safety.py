from __future__ import annotations

import io
import os
import stat
from pathlib import Path

import pytest

import reflexor.security.fs_safety as fs_safety
from reflexor.security.fs_safety import (
    atomic_write_text,
    read_bytes_limited,
    resolve_path_in_workspace,
)


def test_resolve_rejects_traversal_outside_workspace(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="escapes workspace root"):
        resolve_path_in_workspace(Path("../escape.txt"), workspace_root=tmp_path)


def test_resolve_rejects_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("x", encoding="utf-8")

    link = tmp_path / "link"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported in this environment")

    with pytest.raises(ValueError, match="escapes workspace root"):
        resolve_path_in_workspace(link, workspace_root=tmp_path, must_exist=True)


def test_resolve_rejects_missing_target_under_symlinked_parent(tmp_path: Path) -> None:
    outside_dir = tmp_path.parent / "outside-dir"
    outside_dir.mkdir(exist_ok=True)

    link = tmp_path / "link"
    try:
        os.symlink(outside_dir, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported in this environment")

    with pytest.raises(ValueError, match="escapes workspace root"):
        resolve_path_in_workspace(link / "child.txt", workspace_root=tmp_path)


def test_resolve_reports_symlink_loops_as_value_errors(tmp_path: Path) -> None:
    loop = tmp_path / "loop"
    try:
        os.symlink(loop, loop)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported in this environment")

    with pytest.raises(ValueError, match="failed to resolve path within workspace"):
        resolve_path_in_workspace(loop, workspace_root=tmp_path)


def test_atomic_write_replaces_file(tmp_path: Path) -> None:
    target = tmp_path / "file.txt"

    atomic_write_text(target, "hello", workspace_root=tmp_path)
    assert target.read_text(encoding="utf-8") == "hello"

    atomic_write_text(target, "world", workspace_root=tmp_path)
    assert target.read_text(encoding="utf-8") == "world"


def test_atomic_write_preserves_existing_file_mode(tmp_path: Path) -> None:
    target = tmp_path / "mode.txt"
    target.write_text("old", encoding="utf-8")
    target.chmod(0o640)

    atomic_write_text(target, "new", workspace_root=tmp_path)

    assert target.read_text(encoding="utf-8") == "new"
    assert stat.S_IMODE(target.stat().st_mode) == 0o640


def test_read_enforces_max_file_size(tmp_path: Path) -> None:
    target = tmp_path / "big.bin"
    target.write_bytes(b"x" * 100)

    with pytest.raises(ValueError, match="exceeds max_bytes"):
        read_bytes_limited(target, workspace_root=tmp_path, max_bytes=50)


def test_read_enforces_max_file_size_with_bounded_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "growing.bin"
    target.write_bytes(b"x" * 10)

    monkeypatch.setattr(
        Path,
        "open",
        lambda self, mode="r", *args, **kwargs: io.BytesIO(b"x" * 100),
    )

    with pytest.raises(ValueError, match="exceeds max_bytes"):
        read_bytes_limited(target, workspace_root=tmp_path, max_bytes=50)


def test_atomic_write_enforces_max_bytes(tmp_path: Path) -> None:
    target = tmp_path / "too_big.txt"
    with pytest.raises(ValueError, match="exceeds max_bytes"):
        atomic_write_text(target, "x" * 100, workspace_root=tmp_path, max_bytes=10)


def test_atomic_write_fsyncs_parent_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "fsync.txt"
    real_fsync = os.fsync
    fsync_targets: list[str] = []

    def _record_fsync(fd: int) -> None:
        mode = os.fstat(fd).st_mode
        fsync_targets.append("dir" if stat.S_ISDIR(mode) else "file")
        real_fsync(fd)

    monkeypatch.setattr(fs_safety.os, "fsync", _record_fsync)

    atomic_write_text(target, "hello", workspace_root=tmp_path)

    assert "file" in fsync_targets
    assert "dir" in fsync_targets

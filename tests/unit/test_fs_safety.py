from __future__ import annotations

import os
from pathlib import Path

import pytest

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


def test_atomic_write_replaces_file(tmp_path: Path) -> None:
    target = tmp_path / "file.txt"

    atomic_write_text(target, "hello", workspace_root=tmp_path)
    assert target.read_text(encoding="utf-8") == "hello"

    atomic_write_text(target, "world", workspace_root=tmp_path)
    assert target.read_text(encoding="utf-8") == "world"


def test_read_enforces_max_file_size(tmp_path: Path) -> None:
    target = tmp_path / "big.bin"
    target.write_bytes(b"x" * 100)

    with pytest.raises(ValueError, match="exceeds max_bytes"):
        read_bytes_limited(target, workspace_root=tmp_path, max_bytes=50)


def test_atomic_write_enforces_max_bytes(tmp_path: Path) -> None:
    target = tmp_path / "too_big.txt"
    with pytest.raises(ValueError, match="exceeds max_bytes"):
        atomic_write_text(target, "x" * 100, workspace_root=tmp_path, max_bytes=10)

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from reflexor.config import ReflexorSettings
from reflexor.tools.fs_tool import (
    FsListDirArgs,
    FsListDirTool,
    FsReadTextArgs,
    FsReadTextTool,
    FsWriteTextArgs,
    FsWriteTextTool,
)
from reflexor.tools.sdk.tool import ToolContext


def test_read_text_blocks_workspace_escape(tmp_path: Path) -> None:
    tool = FsReadTextTool(settings=ReflexorSettings(workspace_root=tmp_path))
    ctx = ToolContext(workspace_root=tmp_path, dry_run=False, timeout_s=1.0)

    result = asyncio.run(tool.run(FsReadTextArgs(path="../escape.txt"), ctx))
    assert result.ok is False
    assert result.error_code == "WORKSPACE_VIOLATION"


def test_write_text_dry_run_has_no_side_effects(tmp_path: Path) -> None:
    tool = FsWriteTextTool(settings=ReflexorSettings(workspace_root=tmp_path))
    ctx = ToolContext(workspace_root=tmp_path, dry_run=True, timeout_s=1.0)

    target = tmp_path / "note.txt"
    result = asyncio.run(tool.run(FsWriteTextArgs(path="note.txt", text="hello"), ctx))

    assert result.ok is True
    assert target.exists() is False

    assert isinstance(result.data, dict)
    assert result.data["dry_run"] is True
    assert result.data["path"] == "note.txt"
    assert result.data["bytes"] == 5
    assert "sha256" in result.data
    assert "text" not in result.data


def test_write_text_is_atomic_on_replace_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = FsWriteTextTool(settings=ReflexorSettings(workspace_root=tmp_path))
    ctx = ToolContext(workspace_root=tmp_path, dry_run=False, timeout_s=1.0)

    target = tmp_path / "file.txt"
    target.write_text("old", encoding="utf-8")

    import reflexor.security.fs_safety as fs_safety

    def _boom(_src: str | os.PathLike[str], _dst: str | os.PathLike[str]) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(fs_safety.os, "replace", _boom)

    result = asyncio.run(tool.run(FsWriteTextArgs(path="file.txt", text="new"), ctx))
    assert result.ok is False
    assert result.error_code == "TOOL_ERROR"

    assert target.read_text(encoding="utf-8") == "old"
    assert not any(
        item.name.startswith(f".{target.name}.") and item.name.endswith(".tmp")
        for item in tmp_path.iterdir()
    )


def test_read_text_truncates_large_files(tmp_path: Path) -> None:
    target = tmp_path / "big.txt"
    target.write_text("a" * 200, encoding="utf-8")

    tool = FsReadTextTool(
        settings=ReflexorSettings(workspace_root=tmp_path, max_tool_output_bytes=50)
    )
    ctx = ToolContext(workspace_root=tmp_path, dry_run=False, timeout_s=1.0)

    result = asyncio.run(tool.run(FsReadTextArgs(path="big.txt"), ctx))
    assert result.ok is True
    assert isinstance(result.data, dict)
    assert result.data["truncated"] is True
    assert "<truncated>" in result.data["text"]


def test_list_dir_truncates_output(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "b.txt").write_text("b", encoding="utf-8")
    (tmp_path / "c.txt").write_text("c", encoding="utf-8")

    tool = FsListDirTool(settings=ReflexorSettings(workspace_root=tmp_path))
    ctx = ToolContext(workspace_root=tmp_path, dry_run=False, timeout_s=1.0)

    result = asyncio.run(tool.run(FsListDirArgs(path=".", max_entries=2), ctx))
    assert result.ok is True
    assert isinstance(result.data, dict)
    assert result.data["truncated"] is True
    assert len(result.data["items"]) == 2

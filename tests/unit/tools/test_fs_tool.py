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

    result = asyncio.run(tool.run(FsReadTextArgs(path=Path("../escape.txt")), ctx))
    assert result.ok is False
    assert result.error_code == "WORKSPACE_VIOLATION"


def test_write_text_dry_run_has_no_side_effects(tmp_path: Path) -> None:
    tool = FsWriteTextTool(settings=ReflexorSettings(workspace_root=tmp_path))
    ctx = ToolContext(workspace_root=tmp_path, dry_run=True, timeout_s=1.0)

    target = tmp_path / "note.txt"
    result = asyncio.run(tool.run(FsWriteTextArgs(path=Path("note.txt"), text="hello"), ctx))

    assert result.ok is True
    assert target.exists() is False

    assert isinstance(result.data, dict)
    assert result.data["dry_run"] is True
    assert result.data["path"] == "note.txt"
    assert result.data["bytes"] == 5
    assert "sha256" in result.data
    assert "text" not in result.data


def test_write_text_dry_run_does_not_modify_existing_file(tmp_path: Path) -> None:
    tool = FsWriteTextTool(settings=ReflexorSettings(workspace_root=tmp_path))
    ctx = ToolContext(workspace_root=tmp_path, dry_run=True, timeout_s=1.0)

    target = tmp_path / "note.txt"
    target.write_text("old", encoding="utf-8")

    result = asyncio.run(tool.run(FsWriteTextArgs(path=Path("note.txt"), text="new"), ctx))
    assert result.ok is True
    assert target.read_text(encoding="utf-8") == "old"

    assert isinstance(result.data, dict)
    assert result.data["dry_run"] is True
    assert result.data["existed_before"] is True


def test_write_text_blocks_absolute_path_outside_workspace(tmp_path: Path) -> None:
    tool = FsWriteTextTool(settings=ReflexorSettings(workspace_root=tmp_path))
    ctx = ToolContext(workspace_root=tmp_path, dry_run=False, timeout_s=1.0)

    outside = tmp_path.parent / "outside.txt"
    result = asyncio.run(tool.run(FsWriteTextArgs(path=outside, text="x"), ctx))

    assert result.ok is False
    assert result.error_code == "WORKSPACE_VIOLATION"


def test_write_text_creates_file_inside_workspace(tmp_path: Path) -> None:
    tool = FsWriteTextTool(settings=ReflexorSettings(workspace_root=tmp_path))
    ctx = ToolContext(workspace_root=tmp_path, dry_run=False, timeout_s=1.0)

    target = tmp_path / "created.txt"
    assert target.exists() is False

    result = asyncio.run(
        tool.run(FsWriteTextArgs(path=Path("created.txt"), text="hello"), ctx)
    )

    assert result.ok is True
    assert target.read_text(encoding="utf-8") == "hello"

    assert isinstance(result.data, dict)
    assert result.data["dry_run"] is False
    assert result.data["path"] == "created.txt"
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

    def _boom(
        _src: str | os.PathLike[str],
        _dst: str | os.PathLike[str],
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        _ = (src_dir_fd, dst_dir_fd)
        raise OSError("replace failed")

    monkeypatch.setattr(fs_safety.os, "replace", _boom)

    result = asyncio.run(tool.run(FsWriteTextArgs(path=Path("file.txt"), text="new"), ctx))
    assert result.ok is False
    assert result.error_code == "TOOL_ERROR"
    assert result.debug == {"exception_type": "OSError"}
    assert "replace failed" not in str(result.model_dump(mode="json"))

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

    result = asyncio.run(tool.run(FsReadTextArgs(path=Path("big.txt")), ctx))
    assert result.ok is True
    assert isinstance(result.data, dict)
    assert result.data["truncated"] is True
    assert len(result.data["text"].encode("utf-8")) <= 50
    assert "<truncated>" in result.data["text"]


def test_read_text_truncates_multibyte_files_without_decode_failure(tmp_path: Path) -> None:
    target = tmp_path / "emoji.txt"
    target.write_text("😀" * 30, encoding="utf-8")

    tool = FsReadTextTool(
        settings=ReflexorSettings(workspace_root=tmp_path, max_tool_output_bytes=50)
    )
    ctx = ToolContext(workspace_root=tmp_path, dry_run=False, timeout_s=1.0)

    result = asyncio.run(
        tool.run(FsReadTextArgs(path=Path("emoji.txt"), errors="strict"), ctx)
    )

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

    result = asyncio.run(tool.run(FsListDirArgs(path=Path("."), max_entries=2), ctx))
    assert result.ok is True
    assert isinstance(result.data, dict)
    assert result.data["truncated"] is True
    assert len(result.data["items"]) == 2


def test_list_dir_does_not_follow_symlink_targets_for_type_detection(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-dir"
    outside.mkdir(exist_ok=True)

    link = tmp_path / "escape-link"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported in this environment")

    tool = FsListDirTool(settings=ReflexorSettings(workspace_root=tmp_path))
    ctx = ToolContext(workspace_root=tmp_path, dry_run=False, timeout_s=1.0)

    result = asyncio.run(tool.run(FsListDirArgs(path=Path(".")), ctx))

    assert result.ok is True
    assert isinstance(result.data, dict)
    items = {item["name"]: item["type"] for item in result.data["items"]}
    assert items["escape-link"] == "other"

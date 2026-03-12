"""Concrete implementations for built-in filesystem tools."""

from __future__ import annotations

import codecs
import hashlib
import heapq
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from reflexor.config import ReflexorSettings, get_settings
from reflexor.observability.truncation import TRUNCATION_MARKER, truncate_str
from reflexor.security.fs_safety import atomic_write_text, resolve_path_in_workspace
from reflexor.security.scopes import Scope
from reflexor.tools.sdk.contracts import ToolManifest, ToolResult
from reflexor.tools.sdk.tool import ToolContext

_MAX_LIST_ENTRIES_CAP = 1_000


def _normalize_non_empty_str(value: str, *, field_name: str) -> str:
    trimmed = value.strip()
    if not trimmed:
        raise ValueError(f"{field_name} must be non-empty")
    return trimmed


def _display_path(resolved: Path, *, workspace_root: Path) -> str:
    try:
        relative = resolved.relative_to(workspace_root)
    except ValueError:
        return str(resolved)
    return relative.as_posix()


def _decode_text_bytes(
    *,
    raw: bytes,
    encoding: str,
    errors: Literal["strict", "replace", "ignore"],
    truncated: bool,
) -> str:
    decoder_type = codecs.getincrementaldecoder(encoding)
    decoder = decoder_type(errors=errors)
    text = decoder.decode(raw, final=not truncated)
    if not truncated:
        text += decoder.decode(b"", final=True)
    return text


def _dir_entry_kind(entry: os.DirEntry[str]) -> str:
    try:
        if entry.is_dir(follow_symlinks=False):
            return "dir"
        if entry.is_file(follow_symlinks=False):
            return "file"
    except OSError:
        return "other"
    return "other"


def _path_error_code(message: str) -> str:
    if "workspace" in message:
        return "WORKSPACE_VIOLATION"
    return "INVALID_PATH"


class FsReadTextArgs(BaseModel):
    """Arguments for `fs.read_text`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: Path
    encoding: str = "utf-8"
    errors: Literal["strict", "replace", "ignore"] = "replace"

    @field_validator("path", mode="before")
    @classmethod
    def _validate_path_non_empty(cls, value: object) -> object:
        if isinstance(value, str):
            return _normalize_non_empty_str(value, field_name="path")
        return value

    @field_validator("encoding")
    @classmethod
    def _validate_encoding_non_empty(cls, value: str) -> str:
        return _normalize_non_empty_str(value, field_name="encoding")


class FsWriteTextArgs(BaseModel):
    """Arguments for `fs.write_text`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: Path
    text: str
    encoding: str = "utf-8"
    errors: Literal["strict", "replace", "ignore"] = "strict"
    create_parents: bool = True

    @field_validator("path", mode="before")
    @classmethod
    def _validate_path_non_empty(cls, value: object) -> object:
        if isinstance(value, str):
            return _normalize_non_empty_str(value, field_name="path")
        return value

    @field_validator("encoding")
    @classmethod
    def _validate_encoding_non_empty(cls, value: str) -> str:
        return _normalize_non_empty_str(value, field_name="encoding")


class FsListDirArgs(BaseModel):
    """Arguments for `fs.list_dir`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: Path = Field(default_factory=lambda: Path("."))
    include_hidden: bool = False
    max_entries: int = 200

    @field_validator("path", mode="before")
    @classmethod
    def _validate_path_non_empty(cls, value: object) -> object:
        if isinstance(value, str):
            return _normalize_non_empty_str(value, field_name="path")
        return value

    @field_validator("max_entries")
    @classmethod
    def _validate_max_entries(cls, value: int) -> int:
        max_entries = int(value)
        if max_entries <= 0:
            raise ValueError("max_entries must be > 0")
        if max_entries > _MAX_LIST_ENTRIES_CAP:
            raise ValueError(f"max_entries must be <= {_MAX_LIST_ENTRIES_CAP}")
        return max_entries


@dataclass(slots=True)
class FsReadTextTool:
    """Read a UTF-8 (or specified encoding) text file within the workspace root."""

    settings: ReflexorSettings | None = None

    name = "fs.read_text"
    manifest = ToolManifest(
        name=name,
        version="0.1.0",
        description="Read a text file under the workspace root.",
        permission_scope=Scope.FS_READ.value,
        side_effects=False,
        idempotent=True,
        default_timeout_s=5,
        max_output_bytes=64_000,
        tags=["fs"],
    )

    ArgsModel = FsReadTextArgs

    async def run(self, args: FsReadTextArgs, ctx: ToolContext) -> ToolResult:
        settings = self.settings or get_settings()
        max_output_bytes = min(
            int(settings.max_tool_output_bytes), int(self.manifest.max_output_bytes)
        )

        try:
            resolved = resolve_path_in_workspace(
                args.path, workspace_root=ctx.workspace_root, must_exist=False
            )
        except ValueError as exc:
            return ToolResult(ok=False, error_code="WORKSPACE_VIOLATION", error_message=str(exc))

        if not resolved.exists():
            return ToolResult(ok=False, error_code="NOT_FOUND", error_message="path not found")

        if not resolved.is_file():
            return ToolResult(ok=False, error_code="NOT_A_FILE", error_message="path is not a file")

        try:
            with resolved.open("rb") as handle:
                raw = handle.read(max_output_bytes + 1)
        except OSError as exc:
            return ToolResult(
                ok=False,
                error_code="TOOL_ERROR",
                error_message="failed to read file",
                debug={"exception": repr(exc)},
            )

        truncated = len(raw) > max_output_bytes
        try:
            text = _decode_text_bytes(
                raw=raw[:max_output_bytes] if truncated else raw,
                encoding=args.encoding,
                errors=args.errors,
                truncated=truncated,
            )
        except LookupError as exc:
            return ToolResult(
                ok=False,
                error_code="ENCODING_ERROR",
                error_message="unknown text encoding",
                debug={"exception": repr(exc), "encoding": args.encoding},
            )
        except UnicodeDecodeError as exc:
            return ToolResult(
                ok=False,
                error_code="DECODE_ERROR",
                error_message="failed to decode file",
                debug={"exception": repr(exc), "encoding": args.encoding},
            )

        if truncated:
            text = truncate_str(f"{text}{TRUNCATION_MARKER}", max_bytes=max_output_bytes)

        try:
            file_bytes = resolved.stat().st_size
        except OSError:
            file_bytes = None

        return ToolResult(
            ok=True,
            data={
                "path": _display_path(resolved, workspace_root=ctx.workspace_root),
                "truncated": truncated,
                "file_bytes": file_bytes,
                "encoding": args.encoding,
                "text": text,
            },
        )


@dataclass(slots=True)
class FsWriteTextTool:
    """Write a text file within the workspace root (atomic)."""

    settings: ReflexorSettings | None = None

    name = "fs.write_text"
    manifest = ToolManifest(
        name=name,
        version="0.1.0",
        description="Write a text file under the workspace root (atomic).",
        permission_scope=Scope.FS_WRITE.value,
        side_effects=True,
        idempotent=True,
        default_timeout_s=10,
        max_output_bytes=8_000,
        tags=["fs"],
    )

    ArgsModel = FsWriteTextArgs

    async def run(self, args: FsWriteTextArgs, ctx: ToolContext) -> ToolResult:
        settings = self.settings or get_settings()

        try:
            resolved = resolve_path_in_workspace(
                args.path, workspace_root=ctx.workspace_root, must_exist=False
            )
        except ValueError as exc:
            return ToolResult(ok=False, error_code="WORKSPACE_VIOLATION", error_message=str(exc))

        try:
            data = args.text.encode(args.encoding, errors=args.errors)
        except LookupError as exc:
            return ToolResult(
                ok=False,
                error_code="ENCODING_ERROR",
                error_message="unknown text encoding",
                debug={"exception": repr(exc), "encoding": args.encoding},
            )
        except UnicodeEncodeError as exc:
            return ToolResult(
                ok=False,
                error_code="ENCODE_ERROR",
                error_message="failed to encode text",
                debug={"exception": repr(exc), "encoding": args.encoding},
            )

        max_bytes = int(settings.max_event_payload_bytes)
        if len(data) > max_bytes:
            return ToolResult(
                ok=False,
                error_code="BODY_TOO_LARGE",
                error_message=f"text exceeds max bytes ({len(data)} > {max_bytes})",
                debug={"max_bytes": max_bytes},
            )

        content_hash = hashlib.sha256(data).hexdigest()
        existed_before = resolved.exists()

        summary: dict[str, object] = {
            "path": _display_path(resolved, workspace_root=ctx.workspace_root),
            "bytes": len(data),
            "sha256": content_hash,
            "existed_before": existed_before,
            "encoding": args.encoding,
        }

        if ctx.dry_run:
            return ToolResult(ok=True, data={"dry_run": True, **summary})

        try:
            atomic_write_text(
                args.path,
                args.text,
                workspace_root=ctx.workspace_root,
                max_bytes=max_bytes,
                encoding=args.encoding,
                errors=args.errors,
                create_parents=args.create_parents,
            )
        except ValueError as exc:
            return ToolResult(
                ok=False,
                error_code=_path_error_code(str(exc)),
                error_message=str(exc),
            )
        except OSError as exc:
            return ToolResult(
                ok=False,
                error_code="TOOL_ERROR",
                error_message="failed to write file",
                debug={"exception": repr(exc)},
            )

        return ToolResult(ok=True, data={"dry_run": False, **summary})


@dataclass(slots=True)
class FsListDirTool:
    """List directory entries under the workspace root (best-effort)."""

    settings: ReflexorSettings | None = None

    name = "fs.list_dir"
    manifest = ToolManifest(
        name=name,
        version="0.1.0",
        description="List directory entries under the workspace root.",
        permission_scope=Scope.FS_READ.value,
        side_effects=False,
        idempotent=True,
        default_timeout_s=5,
        max_output_bytes=64_000,
        tags=["fs"],
    )

    ArgsModel = FsListDirArgs

    async def run(self, args: FsListDirArgs, ctx: ToolContext) -> ToolResult:
        _ = self.settings or get_settings()

        try:
            resolved = resolve_path_in_workspace(
                args.path, workspace_root=ctx.workspace_root, must_exist=False
            )
        except ValueError as exc:
            return ToolResult(ok=False, error_code="WORKSPACE_VIOLATION", error_message=str(exc))

        if not resolved.exists():
            return ToolResult(ok=False, error_code="NOT_FOUND", error_message="path not found")

        if not resolved.is_dir():
            return ToolResult(
                ok=False, error_code="NOT_A_DIRECTORY", error_message="path is not a directory"
            )

        try:
            with os.scandir(resolved) as it:
                entries = heapq.nsmallest(
                    args.max_entries + 1,
                    (
                        entry
                        for entry in it
                        if args.include_hidden or not entry.name.startswith(".")
                    ),
                    key=lambda item: item.name,
                )
        except OSError as exc:
            return ToolResult(
                ok=False,
                error_code="TOOL_ERROR",
                error_message="failed to list directory",
                debug={"exception": repr(exc)},
            )

        items: list[dict[str, object]] = []
        for entry in entries[: args.max_entries]:
            items.append({"name": entry.name, "type": _dir_entry_kind(entry)})

        truncated = len(entries) > args.max_entries

        return ToolResult(
            ok=True,
            data={
                "path": _display_path(resolved, workspace_root=ctx.workspace_root),
                "truncated": truncated,
                "items": items,
            },
        )


if TYPE_CHECKING:
    from reflexor.tools.sdk.tool import Tool

    _read_tool: Tool[FsReadTextArgs] = FsReadTextTool()
    _write_tool: Tool[FsWriteTextArgs] = FsWriteTextTool()
    _list_tool: Tool[FsListDirArgs] = FsListDirTool()

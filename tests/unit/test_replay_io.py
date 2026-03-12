from __future__ import annotations

from pathlib import Path
from typing import Literal

import pytest

from reflexor.replay.importer import RunPacketImportError, _read_export_file
from reflexor.replay.runner.io import _read_json_file
from reflexor.replay.runner.types import ReplayError


def test_read_json_file_uses_bounded_read(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "packet.json"
    path.write_text("{}", encoding="utf-8")

    requested_sizes: list[int] = []

    class _FakeFile:
        def __enter__(self) -> _FakeFile:
            return self

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            tb: object | None,
        ) -> Literal[False]:
            _ = (exc_type, exc, tb)
            return False

        def read(self, size: int = -1) -> bytes:
            requested_sizes.append(size)
            return b"x" * size

    def _fake_open(self: Path, mode: str = "r", *args: object, **kwargs: object) -> _FakeFile:
        _ = (args, kwargs)
        assert self == path
        assert mode == "rb"
        return _FakeFile()

    monkeypatch.setattr(Path, "open", _fake_open)

    with pytest.raises(ReplayError, match="replay file is too large"):
        _read_json_file(path, max_bytes=4)

    assert requested_sizes == [5]


def test_read_json_file_parses_small_json(tmp_path: Path) -> None:
    path = tmp_path / "packet.json"
    path.write_text('{"schema_version":1}', encoding="utf-8")

    payload = _read_json_file(path, max_bytes=128)

    assert payload == {"schema_version": 1}


def test_read_export_file_uses_bounded_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "export.json"
    path.write_text("{}", encoding="utf-8")

    requested_sizes: list[int] = []

    class _FakeFile:
        def __enter__(self) -> _FakeFile:
            return self

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            tb: object | None,
        ) -> Literal[False]:
            _ = (exc_type, exc, tb)
            return False

        def read(self, size: int = -1) -> bytes:
            requested_sizes.append(size)
            return b"x" * size

    def _fake_open(self: Path, mode: str = "r", *args: object, **kwargs: object) -> _FakeFile:
        _ = (args, kwargs)
        assert self == path
        assert mode == "rb"
        return _FakeFile()

    monkeypatch.setattr(Path, "open", _fake_open)

    with pytest.raises(RunPacketImportError, match="export file is too large"):
        _read_export_file(path, max_bytes=4)

    assert requested_sizes == [5]

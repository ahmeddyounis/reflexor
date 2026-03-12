from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from reflexor.cli.container import CliContainer
from reflexor.cli.main import app
from reflexor.config import ReflexorSettings
from reflexor.replay.importer import RunPacketImportError
from reflexor.replay.runner import ReplayError


def test_runs_export_returns_json_error_for_missing_run() -> None:
    class _MissingRunClient:
        async def export_run_packet(
            self,
            _run_id: str,
            _out_path: str | Path,
            **_kwargs: object,
        ) -> dict[str, object]:
            raise KeyError("unknown run_id: 'missing'")

    container = CliContainer.build(
        settings=ReflexorSettings(profile="dev"),
        client=_MissingRunClient(),  # type: ignore[arg-type]
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["runs", "export", "missing", "--out", "out.json", "--json"],
        obj=container,
    )

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["error_code"] == "not_found"
    assert payload["message"] == "unknown run_id: 'missing'"


def test_runs_import_returns_json_error_for_invalid_packet() -> None:
    class _BadImportClient:
        async def import_run_packet(
            self,
            _path: str | Path,
            **_kwargs: object,
        ) -> dict[str, object]:
            raise RunPacketImportError("invalid JSON: bad payload")

    container = CliContainer.build(
        settings=ReflexorSettings(profile="dev"),
        client=_BadImportClient(),  # type: ignore[arg-type]
    )

    runner = CliRunner()
    result = runner.invoke(app, ["runs", "import", "bad.json", "--json"], obj=container)

    assert result.exit_code == 2
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["error_code"] == "invalid_input"
    assert payload["message"] == "invalid JSON: bad payload"


def test_runs_replay_returns_json_error_for_missing_file() -> None:
    class _MissingReplayFileClient:
        async def replay_run_packet(
            self,
            _path: str | Path,
            **_kwargs: object,
        ) -> dict[str, object]:
            raise FileNotFoundError("missing.json")

    container = CliContainer.build(
        settings=ReflexorSettings(profile="dev"),
        client=_MissingReplayFileClient(),  # type: ignore[arg-type]
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["runs", "replay", "missing.json", "--mode", "dry_run_no_tools", "--json"],
        obj=container,
    )

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["error_code"] == "not_found"
    assert payload["message"] == "missing.json"


def test_runs_replay_returns_json_error_for_invalid_replay_packet() -> None:
    class _BadReplayClient:
        async def replay_run_packet(
            self,
            _path: str | Path,
            **_kwargs: object,
        ) -> dict[str, object]:
            raise ReplayError("exported packet is not a valid RunPacket")

    container = CliContainer.build(
        settings=ReflexorSettings(profile="dev"),
        client=_BadReplayClient(),  # type: ignore[arg-type]
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["runs", "replay", "bad.json", "--mode", "dry_run_no_tools", "--json"],
        obj=container,
    )

    assert result.exit_code == 2
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["error_code"] == "invalid_input"
    assert payload["message"] == "exported packet is not a valid RunPacket"

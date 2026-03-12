from __future__ import annotations

import json

from typer.testing import CliRunner

from reflexor.cli.container import CliContainer
from reflexor.cli.main import app
from reflexor.config import ReflexorSettings


def test_run_api_help_works() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["run", "api", "--help"])

    assert result.exit_code == 0
    assert "Start the Reflexor API server" in result.output
    assert "--host" in result.output
    assert "--port" in result.output


def test_root_api_command_defaults_reload_off_in_json_mode(tmp_path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["api", "--json"],
        obj=CliContainer.build(settings=ReflexorSettings(workspace_root=tmp_path)),
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload == {
        "command": "api",
        "host": "127.0.0.1",
        "ok": True,
        "port": 8000,
        "reload": False,
    }


def test_root_api_command_rejects_reload_outside_dev(tmp_path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["--profile", "prod", "api", "--reload", "--json"],
        obj=CliContainer.build(settings=ReflexorSettings(workspace_root=tmp_path)),
    )

    assert result.exit_code == 2
    payload = json.loads(result.output)
    assert payload == {
        "error_code": "invalid_input",
        "message": "reload is only supported when profile=dev",
        "ok": False,
    }

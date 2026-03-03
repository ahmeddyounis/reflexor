from __future__ import annotations

from typer.testing import CliRunner

from reflexor.cli.main import app


def test_run_api_help_works() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["run", "api", "--help"])

    assert result.exit_code == 0
    assert "Start the Reflexor API server" in result.output
    assert "--host" in result.output
    assert "--port" in result.output


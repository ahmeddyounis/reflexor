from __future__ import annotations

from typer.testing import CliRunner

from reflexor.cli.main import app


def test_run_worker_help_works() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["run", "worker", "--help"])

    assert result.exit_code == 0
    assert "Start the Reflexor worker runner" in result.output
    assert "--concurrency" in result.output


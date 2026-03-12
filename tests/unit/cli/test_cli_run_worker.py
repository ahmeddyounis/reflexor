from __future__ import annotations

from typer.testing import CliRunner

from reflexor.cli.main import app


def test_run_worker_help_works() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["run", "worker", "--help"])

    assert result.exit_code == 0
    assert "Start the Reflexor worker runner" in result.output
    assert "--concurrency" in result.output


def test_run_worker_rejects_non_positive_concurrency() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["run", "worker", "--concurrency", "0"])

    assert result.exit_code == 2
    assert "Invalid value for '--concurrency'" in result.output

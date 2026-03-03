from typer.testing import CliRunner

from reflexor.cli.main import app


def test_cli_help_shows_commands() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Reflexor CLI" in result.output
    assert "Commands" in result.output
    assert "version" in result.output
    assert "api" in result.output

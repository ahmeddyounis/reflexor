from __future__ import annotations

import json

from typer.testing import CliRunner

from reflexor.cli.container import CliContainer
from reflexor.cli.main import app
from reflexor.config import ReflexorSettings


def test_config_show_redacts_secrets_and_is_json_safe() -> None:
    settings = ReflexorSettings(
        profile="dev",
        admin_api_key="super-secret",
        database_url="postgresql+asyncpg://user:dbpass@localhost:5432/reflexor",
        enabled_scopes=["fs.read"],
        http_allowed_domains=["example.test"],
        webhook_allowed_targets=["https://hooks.example.test/ok"],
        queue_backend="inmemory",
    )
    container = CliContainer.build(settings=settings)

    runner = CliRunner()
    result = runner.invoke(app, ["config", "show", "--json"], obj=container)

    assert result.exit_code == 0
    assert "super-secret" not in result.output
    assert "dbpass" not in result.output

    payload = json.loads(result.output)
    assert payload["profile"] == "dev"
    assert payload["enabled_scopes"] == ["fs.read"]
    assert payload["admin_api_key"] == "<redacted>"
    assert "<redacted>" in payload["database_url"]

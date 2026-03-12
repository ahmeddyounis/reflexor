from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from reflexor.cli.container import CliContainer
from reflexor.cli.main import app
from reflexor.config import ReflexorSettings


def test_config_show_redacts_secrets_and_is_json_safe() -> None:
    settings = ReflexorSettings(
        profile="dev",
        admin_api_key="super-secret",
        planner_api_key="planner-secret",
        database_url="postgresql+asyncpg://user:dbpass@localhost:5432/reflexor",
        redis_url="redis://:redis-pass@localhost:6379/0",
        otel_exporter_otlp_endpoint="https://otel:otel-pass@collector.example.test/v1/traces",
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
    assert payload["planner_api_key"] == "<redacted>"
    assert "<redacted>" in payload["database_url"]
    assert "<redacted>" in payload["redis_url"]
    assert "<redacted>" in payload["otel_exporter_otlp_endpoint"]


def test_config_show_handles_malformed_url_passwords_without_crashing() -> None:
    settings = ReflexorSettings(
        database_url="postgresql+asyncpg://user:dbpass@localhost:bad/reflexor",
        workspace_root=Path("."),
    )
    container = CliContainer.build(settings=settings)

    runner = CliRunner()
    result = runner.invoke(app, ["config", "show", "--json"], obj=container)

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert "<redacted>" in payload["database_url"]

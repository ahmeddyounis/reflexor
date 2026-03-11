from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from reflexor.cli.container import CliContainer
from reflexor.cli.main import app
from reflexor.config import ReflexorSettings


def _container(settings: ReflexorSettings) -> CliContainer:
    return CliContainer.build(settings=settings)


def test_config_validate_returns_json_report_for_prod_errors(tmp_path: Path) -> None:
    container = _container(
        ReflexorSettings(
            profile="prod",
            workspace_root=tmp_path,
            database_url="sqlite+aiosqlite:///./reflexor.db",
            queue_backend="inmemory",
        )
    )

    runner = CliRunner()
    result = runner.invoke(app, ["config", "validate", "--json"], obj=container)

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["error_count"] >= 1


def test_config_validate_supports_strict_mode_for_warnings(tmp_path: Path) -> None:
    rules_path = tmp_path / "rules.yaml"
    rules_path.write_text("rules: []\n", encoding="utf-8")
    container = _container(
        ReflexorSettings(
            profile="prod",
            workspace_root=tmp_path,
            admin_api_key="secret",
            events_require_admin=True,
            database_url="postgresql+asyncpg://user:pass@db.example.test:5432/reflexor",
            queue_backend="redis_streams",
            redis_url="redis://redis.example.test:6379/0",
            redis_stream_maxlen=1000,
            planner_backend="heuristic",
            reflex_rules_path=rules_path,
        )
    )

    runner = CliRunner()
    result = runner.invoke(app, ["config", "validate", "--strict", "--json"], obj=container)

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["warning_count"] >= 1
    assert payload["strict_ok"] is False


def test_config_validate_passes_without_strict_when_only_warnings_exist(tmp_path: Path) -> None:
    rules_path = tmp_path / "rules.yaml"
    rules_path.write_text("rules: []\n", encoding="utf-8")
    container = _container(
        ReflexorSettings(
            profile="prod",
            workspace_root=tmp_path,
            admin_api_key="secret",
            events_require_admin=True,
            database_url="postgresql+asyncpg://user:pass@db.example.test:5432/reflexor",
            queue_backend="redis_streams",
            redis_url="redis://redis.example.test:6379/0",
            redis_stream_maxlen=1000,
            planner_backend="heuristic",
            reflex_rules_path=rules_path,
        )
    )

    runner = CliRunner()
    result = runner.invoke(app, ["config", "validate", "--json"], obj=container)

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is True

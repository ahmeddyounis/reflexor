from __future__ import annotations

import json

from typer.testing import CliRunner

from reflexor.application.maintenance_service import MaintenanceOutcome
from reflexor.cli.container import CliContainer
from reflexor.cli.main import app
from reflexor.config import ReflexorSettings


class _FakeMaintenance:
    def __init__(self) -> None:
        self.seen_now_ms: int | None = None
        self.error: Exception | None = None

    async def run_once(self, *, now_ms: int | None = None) -> MaintenanceOutcome:
        if self.error is not None:
            raise self.error
        self.seen_now_ms = now_ms
        return MaintenanceOutcome(
            compacted_run_packets=1,
            pruned_memory_items=2,
            archived_tasks=3,
            pruned_expired_dedupe_keys=4,
        )


class _FakeAppContainer:
    def __init__(self) -> None:
        self.maintenance = _FakeMaintenance()
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


def test_maintenance_run_outputs_json(monkeypatch, tmp_path) -> None:
    fake_app = _FakeAppContainer()

    from reflexor.bootstrap import container as bootstrap_container

    monkeypatch.setattr(
        bootstrap_container.AppContainer,
        "build",
        lambda *, settings: fake_app,
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["maintenance", "run", "--json", "--now-ms", "123"],
        obj=CliContainer.build(settings=ReflexorSettings(workspace_root=tmp_path)),
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload == {
        "archived_tasks": 3,
        "compacted_run_packets": 1,
        "ok": True,
        "pruned_expired_dedupe_keys": 4,
        "pruned_memory_items": 2,
    }
    assert fake_app.maintenance.seen_now_ms == 123
    assert fake_app.closed is True


def test_maintenance_run_rejects_remote_api_mode(tmp_path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["maintenance", "run"],
        obj=CliContainer.build(
            settings=ReflexorSettings(
                workspace_root=tmp_path,
                api_url="https://example.test",
            )
        ),
    )

    assert result.exit_code == 1
    assert "local mode" in result.output


def test_maintenance_run_reports_json_validation_errors(monkeypatch, tmp_path) -> None:
    fake_app = _FakeAppContainer()
    fake_app.maintenance.error = ValueError("now_ms must be >= 0")

    from reflexor.bootstrap import container as bootstrap_container

    monkeypatch.setattr(
        bootstrap_container.AppContainer,
        "build",
        lambda *, settings: fake_app,
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["maintenance", "run", "--json"],
        obj=CliContainer.build(settings=ReflexorSettings(workspace_root=tmp_path)),
    )

    assert result.exit_code == 2
    payload = json.loads(result.output)
    assert payload == {
        "error_code": "invalid_input",
        "message": "now_ms must be >= 0",
        "ok": False,
    }
    assert fake_app.closed is True

from __future__ import annotations

from typer.testing import CliRunner

from reflexor.cli.container import CliContainer
from reflexor.cli.main import app
from reflexor.config import ReflexorSettings


class FakeRunsClient:
    async def list_runs(self, *, limit: int, offset: int, status=None, since_ms=None):  # type: ignore[no-untyped-def]
        _ = (status, since_ms)
        return {"limit": limit, "offset": offset, "total": 0, "items": []}


def test_global_options_override_settings_for_client_factory() -> None:
    seen_settings: list[ReflexorSettings] = []

    def client_factory(settings: ReflexorSettings):  # type: ignore[no-untyped-def]
        seen_settings.append(settings)
        return FakeRunsClient()

    container = CliContainer.build(settings=ReflexorSettings(), client_factory=client_factory)  # type: ignore[arg-type]

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "--profile",
            "prod",
            "--api-url",
            "https://example.test/base",
            "--api-key",
            "k",
            "runs",
            "list",
            "--json",
        ],
        obj=container,
    )

    assert result.exit_code == 0
    assert len(seen_settings) == 1
    assert seen_settings[0].profile == "prod"
    assert seen_settings[0].api_url == "https://example.test/base"
    assert seen_settings[0].admin_api_key == "k"


def test_yes_flag_is_recorded_on_container() -> None:
    container = CliContainer.build(
        settings=ReflexorSettings(),
        client_factory=lambda _settings: FakeRunsClient(),  # type: ignore[arg-type]
    )

    runner = CliRunner()
    result = runner.invoke(app, ["--yes", "version", "--json"], obj=container)

    assert result.exit_code == 0
    assert container.assume_yes is True

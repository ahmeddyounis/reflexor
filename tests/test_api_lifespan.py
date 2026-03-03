from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from reflexor.api.app import create_app
from reflexor.api.container import AppContainer
from reflexor.config import ReflexorSettings


def test_create_app_lifespan_startup_and_shutdown(tmp_path: Path) -> None:
    settings = ReflexorSettings(
        workspace_root=tmp_path,
        enabled_scopes=[],
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'reflexor_api_test.db'}",
    )

    app = create_app(settings=settings)
    assert isinstance(app, FastAPI)

    with TestClient(app) as client:
        response = client.get("/healthz")
        assert response.status_code == 200
        payload = response.json()
        assert payload["ok"] is True
        assert payload["profile"] == "dev"
        assert isinstance(payload["time_ms"], int)
        assert payload["db_ok"] is True

        container = app.state.container
        assert isinstance(container, AppContainer)
        assert container.settings.workspace_root == tmp_path

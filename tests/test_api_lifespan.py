from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from reflexor.api.app import create_app
from reflexor.bootstrap.container import AppContainer
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
        assert payload["queue_ok"] is True

        container = app.state.container
        assert isinstance(container, AppContainer)
        assert container.settings.workspace_root == tmp_path


class _FailingStartupContainer:
    def __init__(
        self,
        *,
        settings: ReflexorSettings,
        startup_error: Exception,
        close_error: Exception | None = None,
    ) -> None:
        self.settings = settings
        self._startup_error = startup_error
        self._close_error = close_error
        self.close_calls = 0

    async def start(self) -> None:
        raise self._startup_error

    async def aclose(self) -> None:
        self.close_calls += 1
        if self._close_error is not None:
            raise self._close_error


def test_create_app_closes_container_when_startup_fails(tmp_path: Path) -> None:
    startup_error = RuntimeError("startup boom")
    container = _FailingStartupContainer(
        settings=ReflexorSettings(workspace_root=tmp_path),
        startup_error=startup_error,
        close_error=RuntimeError("close boom"),
    )
    app = create_app(container=container)

    with pytest.raises(RuntimeError, match="startup boom"):
        with TestClient(app):
            pass

    assert container.close_calls == 1
    assert getattr(app.state, "container", None) is None

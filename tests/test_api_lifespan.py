from __future__ import annotations

import io
import logging
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import reflexor.api.app as api_app_module
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
        close_error=RuntimeError("Bearer sk-startup-close-secret-should-not-leak"),
    )
    app = create_app(container=container)
    stream = io.StringIO()
    logger = api_app_module.logger
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    original_handlers = list(logger.handlers)
    original_level = logger.level
    original_propagate = logger.propagate
    logger.handlers = [handler]
    logger.setLevel(logging.ERROR)
    logger.propagate = False

    try:
        with pytest.raises(RuntimeError, match="startup boom"):
            with TestClient(app):
                pass
    finally:
        logger.handlers = original_handlers
        logger.setLevel(original_level)
        logger.propagate = original_propagate

    assert container.close_calls == 1
    assert getattr(app.state, "container", None) is None
    assert "application startup cleanup failed" in stream.getvalue()
    assert "sk-startup-close-secret-should-not-leak" not in stream.getvalue()

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from reflexor.api.app import create_app
from reflexor.config import ReflexorSettings


def _settings(tmp_path: Path, **overrides: object) -> ReflexorSettings:
    return ReflexorSettings(
        workspace_root=tmp_path,
        enabled_scopes=[],
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'reflexor_api_auth_test.db'}",
        **overrides,
    )


def test_admin_endpoints_allow_without_key_in_dev(tmp_path: Path) -> None:
    app = create_app(settings=_settings(tmp_path, profile="dev", admin_api_key=None))
    with TestClient(app) as client:
        # Endpoint itself is stubbed (501), but should not be blocked by auth in dev without a key.
        response = client.get("/v1/runs")
        assert response.status_code == 501


def test_admin_endpoints_require_key_when_set(tmp_path: Path) -> None:
    app = create_app(settings=_settings(tmp_path, profile="dev", admin_api_key="secret"))
    with TestClient(app) as client:
        assert client.get("/v1/runs").status_code == 401
        assert client.get("/v1/runs", headers={"X-API-Key": "wrong"}).status_code == 401
        assert client.get("/v1/runs", headers={"X-API-Key": "secret"}).status_code == 501


def test_admin_endpoints_denied_without_key_in_prod(tmp_path: Path) -> None:
    app = create_app(settings=_settings(tmp_path, profile="prod", admin_api_key=None))
    with TestClient(app) as client:
        assert client.get("/v1/runs").status_code == 401


def test_events_public_by_default_even_when_admin_key_set(tmp_path: Path) -> None:
    app = create_app(settings=_settings(tmp_path, profile="dev", admin_api_key="secret"))
    with TestClient(app) as client:
        response = client.post("/v1/events", json={"type": "t", "source": "s", "payload": {}})
        assert response.status_code == 501


def test_events_can_be_protected_when_configured(tmp_path: Path) -> None:
    app = create_app(
        settings=_settings(
            tmp_path, profile="dev", admin_api_key="secret", events_require_admin=True
        )
    )
    with TestClient(app) as client:
        response = client.post("/v1/events", json={"type": "t", "source": "s", "payload": {}})
        assert response.status_code == 401

        response = client.post(
            "/v1/events",
            json={"type": "t", "source": "s", "payload": {}},
            headers={"X-API-Key": "secret"},
        )
        assert response.status_code == 501

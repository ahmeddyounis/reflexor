from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from reflexor.api.app import create_app
from reflexor.config import ReflexorSettings
from reflexor.infra.db.models import Base


def _settings(tmp_path: Path, **overrides: object) -> ReflexorSettings:
    return ReflexorSettings(
        workspace_root=tmp_path,
        enabled_scopes=[],
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'reflexor_api_auth_test.db'}",
        **overrides,
    )


def _create_schema(db_path: Path) -> None:
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    engine.dispose()


def test_admin_endpoints_allow_without_key_in_dev(tmp_path: Path) -> None:
    _create_schema(tmp_path / "reflexor_api_auth_test.db")
    app = create_app(settings=_settings(tmp_path, profile="dev", admin_api_key=None))
    with TestClient(app) as client:
        response = client.get("/v1/runs")
        assert response.status_code == 200


def test_admin_endpoints_require_key_when_set(tmp_path: Path) -> None:
    _create_schema(tmp_path / "reflexor_api_auth_test.db")
    app = create_app(settings=_settings(tmp_path, profile="dev", admin_api_key="secret"))
    with TestClient(app) as client:
        assert client.get("/v1/runs").status_code == 401
        assert client.get("/v1/runs", headers={"X-API-Key": "wrong"}).status_code == 401
        assert client.get("/v1/runs", headers={"X-API-Key": "secret"}).status_code == 200


def test_admin_endpoints_denied_without_key_in_prod(tmp_path: Path) -> None:
    app = create_app(settings=_settings(tmp_path, profile="prod", admin_api_key=None))
    with TestClient(app) as client:
        assert client.get("/v1/runs").status_code == 401


def test_events_public_by_default_even_when_admin_key_set(tmp_path: Path) -> None:
    app = create_app(settings=_settings(tmp_path, profile="dev", admin_api_key="secret"))
    with TestClient(app) as client:
        response = client.get("/v1/events")
        assert response.status_code == 501


def test_events_can_be_protected_when_configured(tmp_path: Path) -> None:
    app = create_app(
        settings=_settings(
            tmp_path, profile="dev", admin_api_key="secret", events_require_admin=True
        )
    )
    with TestClient(app) as client:
        response = client.get("/v1/events")
        assert response.status_code == 401

        response = client.get("/v1/events", headers={"X-API-Key": "secret"})
        assert response.status_code == 501

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

redis = pytest.importorskip("redis")

from reflexor.api.app import create_app  # noqa: E402
from reflexor.api.container import AppContainer  # noqa: E402
from reflexor.config import ReflexorSettings  # noqa: E402
from reflexor.infra.db.models import Base  # noqa: E402


def _create_schema(db_path: Path) -> None:
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    engine.dispose()


def _redis_url() -> str:
    url = os.environ.get("TEST_REDIS_URL") or os.environ.get("REDIS_URL")
    if not url:
        pytest.skip("TEST_REDIS_URL or REDIS_URL is not set")
    return url.strip()


def test_healthz_reports_queue_ok_when_redis_is_reachable(tmp_path: Path) -> None:
    db_path = tmp_path / "reflexor_api_health_queue_ok.db"
    _create_schema(db_path)

    settings = ReflexorSettings(
        workspace_root=tmp_path,
        enabled_scopes=[],
        database_url=f"sqlite+aiosqlite:///{db_path}",
        queue_backend="redis_streams",
        redis_url=_redis_url(),
        redis_stream_key=f"test:reflexor:healthz:stream:{uuid4().hex}",
        redis_delayed_zset_key=f"test:reflexor:healthz:delayed:{uuid4().hex}",
        redis_consumer_group=f"test:reflexor:healthz:group:{uuid4().hex}",
        redis_consumer_name=f"test:reflexor:healthz:consumer:{uuid4().hex}",
    )
    container = AppContainer.build(settings=settings)
    app = create_app(container=container)

    with TestClient(app) as client:
        health = client.get("/healthz")
        assert health.status_code == 200
        payload = health.json()
        assert payload["ok"] is True
        assert payload["db_ok"] is True
        assert payload["queue_ok"] is True
        assert payload["queue_backend"] == "redis_streams"


def test_healthz_reports_queue_not_ok_when_redis_is_unreachable(tmp_path: Path) -> None:
    db_path = tmp_path / "reflexor_api_health_queue_down.db"
    _create_schema(db_path)

    unreachable = "redis://127.0.0.1:6399/0"
    settings = ReflexorSettings(
        workspace_root=tmp_path,
        enabled_scopes=[],
        database_url=f"sqlite+aiosqlite:///{db_path}",
        queue_backend="redis_streams",
        redis_url=unreachable,
        redis_stream_key=f"test:reflexor:healthz:stream:{uuid4().hex}",
        redis_delayed_zset_key=f"test:reflexor:healthz:delayed:{uuid4().hex}",
        redis_consumer_group=f"test:reflexor:healthz:group:{uuid4().hex}",
        redis_consumer_name=f"test:reflexor:healthz:consumer:{uuid4().hex}",
    )
    container = AppContainer.build(settings=settings)
    app = create_app(container=container)

    with TestClient(app) as client:
        health = client.get("/healthz")
        assert health.status_code == 503
        payload = health.json()
        assert payload["ok"] is False
        assert payload["db_ok"] is True
        assert payload["queue_ok"] is False
        assert payload["queue_backend"] == "redis_streams"

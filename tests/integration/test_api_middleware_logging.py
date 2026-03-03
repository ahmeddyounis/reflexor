from __future__ import annotations

import io
import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from reflexor.api.app import create_app
from reflexor.config import ReflexorSettings
from reflexor.infra.db.models import Base
from reflexor.observability.logging import build_json_handler


def _create_schema(db_path: Path) -> None:
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    engine.dispose()


@contextmanager
def _capture_middleware_logs(settings: ReflexorSettings) -> Iterator[io.StringIO]:
    stream = io.StringIO()
    handler = build_json_handler(settings=settings, stream=stream)

    target = logging.getLogger("reflexor.api.middleware")
    original_handlers = list(target.handlers)
    original_level = target.level
    original_propagate = target.propagate

    target.handlers = [handler]
    target.setLevel(logging.INFO)
    target.propagate = False

    try:
        yield stream
    finally:
        target.handlers = original_handlers
        target.setLevel(original_level)
        target.propagate = original_propagate


def _json_lines(stream: io.StringIO) -> list[dict[str, object]]:
    lines = [line for line in stream.getvalue().splitlines() if line.strip()]
    return [json.loads(line) for line in lines]


def test_middleware_echoes_request_id_and_logs_latency(tmp_path: Path) -> None:
    db_path = tmp_path / "reflexor_api_middleware_logs.db"
    _create_schema(db_path)

    settings = ReflexorSettings(
        workspace_root=tmp_path,
        enabled_scopes=[],
        database_url=f"sqlite+aiosqlite:///{db_path}",
    )
    app = create_app(settings=settings)

    with _capture_middleware_logs(settings) as stream:
        with TestClient(app) as client:
            response = client.get("/healthz", headers={"X-Request-ID": "req-123"})
            assert response.status_code == 200
            assert response.headers.get("X-Request-ID") == "req-123"

    payloads = _json_lines(stream)
    end_logs = [payload for payload in payloads if payload.get("event_type") == "api.request.end"]
    assert end_logs, payloads
    last = end_logs[-1]

    assert last.get("request_id") == "req-123"
    assert last.get("status_code") == 200
    assert isinstance(last.get("elapsed_ms"), int)


def test_middleware_rejects_oversized_events_without_traceback(tmp_path: Path) -> None:
    db_path = tmp_path / "reflexor_api_middleware_oversize.db"
    _create_schema(db_path)

    settings = ReflexorSettings(
        workspace_root=tmp_path,
        enabled_scopes=[],
        database_url=f"sqlite+aiosqlite:///{db_path}",
        max_event_payload_bytes=40,
    )
    app = create_app(settings=settings)

    oversized = {
        "type": "webhook",
        "source": "tests",
        "payload": {"blob": "x" * 200},
    }

    with _capture_middleware_logs(settings) as stream:
        with TestClient(app) as client:
            response = client.post("/v1/events", json=oversized)
            assert response.status_code == 413
            assert response.headers.get("X-Request-ID")

    raw = stream.getvalue()
    assert "Traceback" not in raw

    payloads = _json_lines(stream)
    end_logs = [payload for payload in payloads if payload.get("event_type") == "api.request.end"]
    assert end_logs, payloads
    last = end_logs[-1]
    assert last.get("status_code") == 413
    assert isinstance(last.get("elapsed_ms"), int)

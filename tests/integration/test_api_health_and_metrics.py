from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from reflexor.api.app import create_app
from reflexor.api.container import AppContainer
from reflexor.config import ReflexorSettings
from reflexor.infra.db.models import Base


def _create_schema(db_path: Path) -> None:
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    engine.dispose()


def _get_metric_value(text: str, name: str) -> float | None:
    pattern = re.compile(rf"^{re.escape(name)}\s+([0-9eE+\-\.]+)$")
    for line in text.splitlines():
        match = pattern.match(line)
        if match:
            return float(match.group(1))
    return None


def test_healthz_and_metrics_endpoints(tmp_path: Path) -> None:
    db_path = tmp_path / "reflexor_api_health_metrics.db"
    _create_schema(db_path)

    settings = ReflexorSettings(
        workspace_root=tmp_path,
        enabled_scopes=[],
        database_url=f"sqlite+aiosqlite:///{db_path}",
    )
    container = AppContainer.build(settings=settings)
    app = create_app(container=container)

    with TestClient(app) as client:
        health = client.get("/healthz")
        assert health.status_code == 200
        payload = health.json()
        assert payload["ok"] is True
        assert payload["db_ok"] is True
        assert payload["profile"] == "dev"
        assert isinstance(payload["time_ms"], int)

        metrics_before = client.get("/metrics")
        assert metrics_before.status_code == 200
        assert metrics_before.headers["content-type"].startswith("text/plain")
        text_before = metrics_before.text
        assert "events_received_total" in text_before
        assert "event_to_enqueue_seconds" in text_before
        assert "planner_latency_seconds" in text_before
        assert "tool_latency_seconds" in text_before
        assert "tasks_completed_total" in text_before
        assert "policy_decisions_total" in text_before
        assert "queue_depth" in text_before
        assert "queue_redeliver_total" in text_before
        assert "event_ingest_latency_seconds" in text_before
        assert "approvals_pending_total" in text_before
        assert "api_requests_total" in text_before

        assert _get_metric_value(text_before, "events_received_total") == 0.0
        assert _get_metric_value(text_before, "approvals_pending_total") == 0.0

        event_body = {
            "type": "webhook",
            "source": "tests",
            "payload": {},
            "dedupe_key": "k-1",
            "received_at_ms": 123,
        }
        submitted = client.post("/v1/events", json=event_body)
        assert submitted.status_code == 202

        metrics_after = client.get("/metrics")
        assert metrics_after.status_code == 200
        text_after = metrics_after.text
        assert _get_metric_value(text_after, "events_received_total") == 1.0

        latency_count = _get_metric_value(text_after, "event_ingest_latency_seconds_count")
        assert latency_count == 1.0

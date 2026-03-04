from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from reflexor.api.app import create_app
from reflexor.bootstrap.container import AppContainer
from reflexor.config import ReflexorSettings
from reflexor.infra.db.models import Base
from reflexor.orchestrator.clock import Clock
from reflexor.orchestrator.reflex_rules import RuleBasedReflexRouter


@dataclass(slots=True)
class _MutableClock(Clock):
    now: int = 1_000

    def now_ms(self) -> int:
        return self.now

    def monotonic_ms(self) -> int:
        return int(time.monotonic() * 1000)

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


def _create_schema(db_path: Path) -> None:
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    engine.dispose()


def test_event_suppression_blocks_task_enqueue_and_expires(tmp_path: Path) -> None:
    db_path = tmp_path / "reflexor_event_suppression.db"
    _create_schema(db_path)

    clock = _MutableClock(now=1_000)
    settings = ReflexorSettings(
        workspace_root=tmp_path,
        enabled_scopes=[],
        database_url=f"sqlite+aiosqlite:///{db_path}",
        event_suppression_enabled=True,
        event_suppression_signature_fields=["ticket"],
        event_suppression_window_s=60.0,
        event_suppression_threshold=2,
        event_suppression_ttl_s=5.0,
        max_run_packet_bytes=50_000,
    )

    router = RuleBasedReflexRouter.from_raw_rules(
        [
            {
                "rule_id": "readme",
                "match": {"event_type": "webhook"},
                "action": {
                    "kind": "fast_tool",
                    "tool_name": "fs.read_text",
                    "args_template": {"path": "README.md"},
                },
            }
        ]
    )

    container = AppContainer.build(settings=settings, reflex_router=router, clock=clock)
    app = create_app(container=container)

    body = {
        "type": "webhook",
        "source": "tests",
        "payload": {"ticket": "T-1"},
    }

    with TestClient(app) as client:
        first = client.post("/v1/events", json={**body, "payload": {**body["payload"], "seq": 1}})
        assert first.status_code == 202
        run_id_1 = str(first.json()["run_id"])

        second = client.post("/v1/events", json={**body, "payload": {**body["payload"], "seq": 2}})
        assert second.status_code == 202
        run_id_2 = str(second.json()["run_id"])

        third = client.post("/v1/events", json={**body, "payload": {**body["payload"], "seq": 3}})
        assert third.status_code == 202
        suppressed_run_id = str(third.json()["run_id"])

        tasks = client.get("/v1/tasks", params={"limit": 0})
        assert tasks.status_code == 200
        assert tasks.json()["total"] == 2

        suppressions = client.get("/v1/suppressions")
        assert suppressions.status_code == 200
        listed = suppressions.json()
        assert listed["total"] == 1
        assert listed["items"][0]["event_type"] == "webhook"
        assert listed["items"][0]["event_source"] == "tests"
        assert listed["items"][0]["suppressed_until_ms"] is not None

        suppressed_run = client.get(f"/v1/runs/{suppressed_run_id}")
        assert suppressed_run.status_code == 200
        assert suppressed_run.json()["run_packet"]["reflex_decision"]["action"] == "suppressed"

        metrics = client.get("/metrics")
        assert metrics.status_code == 200
        assert "suppressed_events_total 1.0" in metrics.text

        first_run = client.get(f"/v1/runs/{run_id_1}")
        assert first_run.status_code == 200
        assert first_run.json()["run_packet"]["reflex_decision"]["action"] in {
            "fast_tasks",
            "needs_planning",
        }

        second_run = client.get(f"/v1/runs/{run_id_2}")
        assert second_run.status_code == 200
        assert second_run.json()["run_packet"]["reflex_decision"]["action"] in {
            "fast_tasks",
            "needs_planning",
        }

        # Advance past suppression TTL and ensure tasks resume.
        clock.now += 6_000

        resumed = client.post("/v1/events", json={**body, "payload": {**body["payload"], "seq": 4}})
        assert resumed.status_code == 202

        suppressions = client.get("/v1/suppressions")
        assert suppressions.status_code == 200
        assert suppressions.json()["total"] == 0

        tasks = client.get("/v1/tasks", params={"limit": 0})
        assert tasks.status_code == 200
        assert tasks.json()["total"] == 3


def test_event_suppression_clear_endpoint_resumes_immediately(tmp_path: Path) -> None:
    db_path = tmp_path / "reflexor_event_suppression_clear.db"
    _create_schema(db_path)

    clock = _MutableClock(now=1_000)
    settings = ReflexorSettings(
        workspace_root=tmp_path,
        enabled_scopes=[],
        database_url=f"sqlite+aiosqlite:///{db_path}",
        event_suppression_enabled=True,
        event_suppression_signature_fields=["ticket"],
        event_suppression_window_s=60.0,
        event_suppression_threshold=2,
        event_suppression_ttl_s=30.0,
        max_run_packet_bytes=50_000,
    )

    router = RuleBasedReflexRouter.from_raw_rules(
        [
            {
                "rule_id": "readme",
                "match": {"event_type": "webhook"},
                "action": {
                    "kind": "fast_tool",
                    "tool_name": "fs.read_text",
                    "args_template": {"path": "README.md"},
                },
            }
        ]
    )

    container = AppContainer.build(settings=settings, reflex_router=router, clock=clock)
    app = create_app(container=container)

    body = {
        "type": "webhook",
        "source": "tests",
        "payload": {"ticket": "T-1"},
    }

    signature_hash: str
    clear_request_id = "test-clear-request-id"

    with TestClient(app) as client:
        assert (
            client.post(
                "/v1/events", json={**body, "payload": {**body["payload"], "seq": 1}}
            ).status_code
            == 202
        )
        assert (
            client.post(
                "/v1/events", json={**body, "payload": {**body["payload"], "seq": 2}}
            ).status_code
            == 202
        )
        assert (
            client.post(
                "/v1/events", json={**body, "payload": {**body["payload"], "seq": 3}}
            ).status_code
            == 202
        )

        tasks = client.get("/v1/tasks", params={"limit": 0})
        assert tasks.status_code == 200
        assert tasks.json()["total"] == 2

        suppressions = client.get("/v1/suppressions")
        assert suppressions.status_code == 200
        listed = suppressions.json()
        assert listed["total"] == 1
        signature_hash = str(listed["items"][0]["signature_hash"])

        cleared = client.post(
            f"/v1/suppressions/{signature_hash}/clear",
            headers={"X-Request-ID": clear_request_id},
            json={"cleared_by": "operator@example.com"},
        )
        assert cleared.status_code == 200
        assert cleared.headers.get("X-Request-ID") == clear_request_id
        assert cleared.json()["ok"] is True
        assert cleared.json()["signature_hash"] == signature_hash
        assert cleared.json()["cleared_by"] == "operator@example.com"
        assert int(cleared.json()["cleared_at_ms"]) == 1_000

        suppressions = client.get("/v1/suppressions")
        assert suppressions.status_code == 200
        assert suppressions.json()["total"] == 0

        resumed = client.post("/v1/events", json={**body, "payload": {**body["payload"], "seq": 4}})
        assert resumed.status_code == 202

        tasks = client.get("/v1/tasks", params={"limit": 0})
        assert tasks.status_code == 200
        assert tasks.json()["total"] == 3

    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    try:
        with engine.connect() as conn:
            row = (
                conn.execute(
                    text(
                        "SELECT cleared_at_ms, cleared_by, cleared_request_id "
                        "FROM event_suppressions WHERE signature_hash = :hash"
                    ),
                    {"hash": signature_hash},
                )
                .mappings()
                .one()
            )
            assert row["cleared_at_ms"] == 1_000
            assert row["cleared_by"] == "operator@example.com"
            assert row["cleared_request_id"] == clear_request_id
    finally:
        engine.dispose()

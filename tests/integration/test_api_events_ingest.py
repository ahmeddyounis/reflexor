from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from reflexor.api.app import create_app
from reflexor.bootstrap.container import AppContainer
from reflexor.config import ReflexorSettings
from reflexor.domain.enums import TaskStatus
from reflexor.infra.db.models import Base, EventRow, RunPacketRow, RunRow, TaskRow
from reflexor.orchestrator.reflex_rules import RuleBasedReflexRouter


def _create_schema(db_path: Path) -> None:
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    engine.dispose()


def _count_rows(session: Session, table: type[Base]) -> int:
    return int(session.execute(select(func.count()).select_from(table)).scalar_one())


def test_post_events_persists_reflex_run_and_dedupes(tmp_path: Path) -> None:
    db_path = tmp_path / "reflexor_api_events_ingest.db"
    _create_schema(db_path)

    settings = ReflexorSettings(
        workspace_root=tmp_path,
        enabled_scopes=["fs.read"],
        database_url=f"sqlite+aiosqlite:///{db_path}",
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

    container = AppContainer.build(settings=settings, reflex_router=router)
    app = create_app(container=container)

    body = {
        "type": "webhook",
        "source": "tests",
        "payload": {},
        "dedupe_key": "ticket:T-1",
        "received_at_ms": 123,
    }

    with TestClient(app) as client:
        first = client.post("/v1/events", json=body)
        assert first.status_code == 202
        assert first.headers.get("X-Request-ID")
        first_payload = first.json()
        assert first_payload["ok"] is True
        assert first_payload["duplicate"] is False

        event_id = str(first_payload["event_id"])
        run_id = str(first_payload["run_id"])

        second = client.post("/v1/events", json={**body, "payload": {"seq": 2}})
        assert second.status_code == 200
        second_payload = second.json()
        assert second_payload["ok"] is True
        assert second_payload["duplicate"] is True
        assert second_payload["event_id"] == event_id
        assert second_payload["run_id"] == run_id

    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    with Session(engine) as session:
        assert _count_rows(session, EventRow) == 1
        assert _count_rows(session, RunRow) == 1
        assert _count_rows(session, TaskRow) == 1
        assert _count_rows(session, RunPacketRow) == 1

        task_row = session.execute(select(TaskRow)).scalar_one()
        assert task_row.status == TaskStatus.QUEUED.value

    engine.dispose()


def test_post_events_dedupe_uses_server_time_not_client_received_at(tmp_path: Path) -> None:
    db_path = tmp_path / "reflexor_api_events_server_time.db"
    _create_schema(db_path)

    settings = ReflexorSettings(
        workspace_root=tmp_path,
        enabled_scopes=["fs.read"],
        database_url=f"sqlite+aiosqlite:///{db_path}",
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

    container = AppContainer.build(settings=settings, reflex_router=router)
    app = create_app(container=container)

    first_body = {
        "type": "webhook",
        "source": "tests",
        "payload": {"seq": 1},
        "dedupe_key": "ticket:T-2",
        "received_at_ms": 123,
    }
    second_body = {
        **first_body,
        "payload": {"seq": 2},
        "received_at_ms": 9_999_999_999_999,
    }

    with TestClient(app) as client:
        first = client.post("/v1/events", json=first_body)
        assert first.status_code == 202
        first_payload = first.json()

        second = client.post("/v1/events", json=second_body)
        assert second.status_code == 200
        second_payload = second.json()
        assert second_payload["duplicate"] is True
        assert second_payload["event_id"] == first_payload["event_id"]
        assert second_payload["run_id"] == first_payload["run_id"]

    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    with Session(engine) as session:
        assert _count_rows(session, EventRow) == 1
        assert _count_rows(session, RunRow) == 1
        assert _count_rows(session, TaskRow) == 1
        assert _count_rows(session, RunPacketRow) == 1
    engine.dispose()


def test_post_events_enforces_body_size_cap(tmp_path: Path) -> None:
    db_path = tmp_path / "reflexor_api_events_size_cap.db"
    _create_schema(db_path)

    settings = ReflexorSettings(
        workspace_root=tmp_path,
        enabled_scopes=[],
        database_url=f"sqlite+aiosqlite:///{db_path}",
        max_event_payload_bytes=40,
    )
    container = AppContainer.build(settings=settings)
    app = create_app(container=container)

    oversized = {
        "type": "webhook",
        "source": "tests",
        "payload": {"blob": "x" * 200},
    }

    with TestClient(app) as client:
        response = client.post("/v1/events", json=oversized)
        assert response.status_code == 413
        assert response.headers.get("X-Request-ID")

    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    with Session(engine) as session:
        assert _count_rows(session, EventRow) == 0
        assert _count_rows(session, RunRow) == 0
    engine.dispose()

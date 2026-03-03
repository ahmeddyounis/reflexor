from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from reflexor.api.app import create_app
from reflexor.api.container import AppContainer
from reflexor.config import ReflexorSettings
from reflexor.infra.db.models import Base
from reflexor.orchestrator.clock import Clock
from reflexor.orchestrator.reflex_rules import RuleBasedReflexRouter


@dataclass(slots=True)
class _FixedNowClock(Clock):
    now: int = 0

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


def test_runs_list_and_detail_are_admin_protected_and_sanitized(tmp_path: Path) -> None:
    db_path = tmp_path / "reflexor_api_runs_read.db"
    _create_schema(db_path)

    clock = _FixedNowClock(now=1_000)
    settings = ReflexorSettings(
        workspace_root=tmp_path,
        enabled_scopes=[],
        database_url=f"sqlite+aiosqlite:///{db_path}",
        admin_api_key="secret",
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

    with TestClient(app) as client:
        # Create two runs with deterministic timestamps.
        first = client.post(
            "/events",
            json={
                "type": "webhook",
                "source": "tests",
                "payload": {"authorization": "Bearer SUPERSECRETTOKENVALUE", "seq": 1},
                "received_at_ms": 1_000,
            },
        )
        assert first.status_code == 202
        run_id_1 = str(first.json()["run_id"])

        clock.now = 2_000
        second = client.post(
            "/events",
            json={
                "type": "webhook",
                "source": "tests",
                "payload": {"authorization": "Bearer SUPERSECRETTOKENVALUE", "seq": 2},
                "received_at_ms": 2_000,
            },
        )
        assert second.status_code == 202
        run_id_2 = str(second.json()["run_id"])

        # Admin auth: missing key is rejected.
        assert client.get("/runs").status_code == 401

        headers = {"X-API-Key": "secret"}

        listed = client.get("/runs", headers=headers, params={"limit": 1, "offset": 0})
        assert listed.status_code == 200
        listed_payload = listed.json()
        assert listed_payload["total"] == 2
        assert [item["run_id"] for item in listed_payload["items"]] == [run_id_2]

        paged = client.get("/runs", headers=headers, params={"limit": 1, "offset": 1})
        assert paged.status_code == 200
        paged_payload = paged.json()
        assert paged_payload["total"] == 2
        assert [item["run_id"] for item in paged_payload["items"]] == [run_id_1]

        since_filtered = client.get("/runs", headers=headers, params={"since_ms": 1_500})
        assert since_filtered.status_code == 200
        assert [item["run_id"] for item in since_filtered.json()["items"]] == [run_id_2]

        detail = client.get(f"/runs/{run_id_1}", headers=headers)
        assert detail.status_code == 200
        detail_payload = detail.json()
        assert detail_payload["summary"]["run_id"] == run_id_1
        assert detail_payload["run_packet"]["event"]["payload"]["authorization"] == "<redacted>", (
            "run packet payload should be sanitized for audit"
        )

        missing = client.get("/runs/00000000-0000-4000-8000-000000000000", headers=headers)
        assert missing.status_code == 404

    # Sanity-check that the DB file is readable after API shutdown.
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    with Session(engine) as session:
        assert session.execute(text("SELECT COUNT(*) FROM runs")).scalar_one() == 2
    engine.dispose()

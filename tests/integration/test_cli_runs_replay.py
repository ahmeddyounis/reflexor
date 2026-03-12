from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import cast

from sqlalchemy import create_engine
from typer.testing import CliRunner

from reflexor.bootstrap.container import AppContainer
from reflexor.cli.client import LocalClient
from reflexor.cli.container import CliContainer
from reflexor.cli.main import app
from reflexor.config import ReflexorSettings
from reflexor.domain.enums import TaskStatus, ToolCallStatus
from reflexor.domain.models import Task, ToolCall
from reflexor.domain.models_event import Event
from reflexor.domain.models_run_packet import RunPacket
from reflexor.infra.db.models import Base
from reflexor.storage.ports import RunRecord
from reflexor.storage.uow import DatabaseSession


def _create_schema(db_path: Path) -> None:
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    engine.dispose()


def _uuid() -> str:
    return str(uuid.uuid4())


async def _seed_run(container: AppContainer, *, run_id: str) -> None:
    created_at_ms = 1_000
    event = Event(
        event_id=_uuid(),
        type="tests.seeded",
        source="tests",
        received_at_ms=created_at_ms,
        payload={"authorization": "Bearer SUPERSECRET", "seq": 1},
    )

    tool_call = ToolCall(
        tool_call_id=_uuid(),
        tool_name="tests.mock",
        args={"message": "hello"},
        permission_scope="debug.echo",
        idempotency_key="k1",
        status=ToolCallStatus.PENDING,
        created_at_ms=created_at_ms,
    )
    task = Task(
        task_id=_uuid(),
        run_id=run_id,
        name="seeded",
        status=TaskStatus.PENDING,
        tool_call=tool_call,
        created_at_ms=created_at_ms,
    )

    packet = RunPacket(
        run_id=run_id,
        event=event,
        tasks=[task],
        created_at_ms=created_at_ms,
    )

    run_record = RunRecord(
        run_id=run_id,
        parent_run_id=None,
        created_at_ms=packet.created_at_ms,
        started_at_ms=packet.started_at_ms,
        completed_at_ms=packet.completed_at_ms,
    )

    uow = container.uow_factory()
    async with uow:
        session = cast(DatabaseSession, uow.session)
        await container.repos.run_repo(session).create(run_record)
        await container.repos.run_packet_repo(session).create(packet)


def test_cli_runs_export_import_replay_flow(tmp_path: Path) -> None:
    db_path = tmp_path / "reflexor_cli_runs.db"
    _create_schema(db_path)

    settings = ReflexorSettings(
        profile="dev",
        workspace_root=tmp_path,
        database_url=f"sqlite+aiosqlite:///{db_path}",
        max_run_packet_bytes=100_000,
    )

    container = AppContainer.build(settings=settings)
    try:
        run_id = _uuid()
        asyncio.run(_seed_run(container, run_id=run_id))

        local_client = LocalClient(
            settings=settings,
            submitter=container.submit_events,
            run_queries=container.run_queries,
            task_queries=container.task_queries,
            approval_commands=container.approval_commands,
            suppression_queries=container.suppression_queries,
            suppression_commands=container.suppression_commands,
            tool_registry=container.tool_registry,
        )
        cli_container = CliContainer.build(settings=settings, client=local_client)

        runner = CliRunner()

        export_path = tmp_path / "export.json"
        exported = runner.invoke(
            app,
            ["runs", "export", run_id, "--out", str(export_path), "--json"],
            obj=cli_container,
        )
        assert exported.exit_code == 0, exported.output
        export_payload = json.loads(exported.output)
        assert export_payload["ok"] is True
        assert export_payload["run_id"] == run_id
        assert export_payload["out_path"] == str(export_path)
        assert export_path.exists()

        exported_file = json.loads(export_path.read_text(encoding="utf-8"))
        assert exported_file["schema_version"] == 1
        assert exported_file["packet"]["run_id"] == run_id

        imported = runner.invoke(
            app,
            ["runs", "import", str(export_path), "--json"],
            obj=cli_container,
        )
        assert imported.exit_code == 0, imported.output
        import_payload = json.loads(imported.output)
        imported_run_id = str(import_payload["run_id"])
        assert import_payload["ok"] is True
        assert imported_run_id and imported_run_id != run_id

        imported_show = runner.invoke(
            app,
            ["runs", "show", imported_run_id, "--json"],
            obj=cli_container,
        )
        assert imported_show.exit_code == 0, imported_show.output
        imported_show_payload = json.loads(imported_show.output)
        assert imported_show_payload["run"]["summary"]["run_id"] == imported_run_id
        assert imported_show_payload["run"]["run_packet"]["run_id"] == imported_run_id
        assert imported_show_payload["run"]["run_packet"]["parent_run_id"] == run_id
        assert imported_show_payload["tasks"]["total"] == 1
        assert imported_show_payload["tasks"]["items"][0]["run_id"] == imported_run_id

        replayed = runner.invoke(
            app,
            ["runs", "replay", str(export_path), "--mode", "dry_run_no_tools", "--json"],
            obj=cli_container,
        )
        assert replayed.exit_code == 0, replayed.output
        replay_payload = json.loads(replayed.output)
        replay_run_id = str(replay_payload["run_id"])
        assert replay_payload["ok"] is True
        assert replay_payload["parent_run_id"] == run_id
        assert replay_payload["mode"] == "dry_run_no_tools"

        replay_show = runner.invoke(
            app,
            ["runs", "show", replay_run_id, "--json"],
            obj=cli_container,
        )
        assert replay_show.exit_code == 0, replay_show.output
        replay_show_payload = json.loads(replay_show.output)
        assert replay_show_payload["run"]["summary"]["run_id"] == replay_run_id
        assert replay_show_payload["run"]["run_packet"]["parent_run_id"] == run_id
    finally:
        asyncio.run(container.aclose())


def test_cli_runs_replay_requires_yes_in_prod(tmp_path: Path) -> None:
    settings = ReflexorSettings(profile="prod", workspace_root=tmp_path)
    cli_container = CliContainer.build(settings=settings, client=object())  # type: ignore[arg-type]

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["runs", "replay", str(tmp_path / "packet.json"), "--mode", "dry_run_no_tools", "--json"],
        obj=cli_container,
    )

    assert result.exit_code == 2
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["error_code"] == "confirmation_required"

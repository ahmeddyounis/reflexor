from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from reflexor.api.app import create_app
from reflexor.bootstrap.container import AppContainer
from reflexor.config import ReflexorSettings
from reflexor.domain.enums import TaskStatus, ToolCallStatus
from reflexor.domain.models import Task, ToolCall
from reflexor.infra.db.models import Base
from reflexor.storage.ports import RunRecord


def _uuid() -> str:
    return str(uuid.uuid4())


def _create_schema(db_path: Path) -> None:
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    engine.dispose()


async def _seed_tasks(container: AppContainer) -> tuple[str, str, str, str, str]:
    run_id_1 = _uuid()
    run_id_2 = _uuid()

    task_id_1 = _uuid()
    task_id_2 = _uuid()
    task_id_3 = _uuid()

    tool_call_1 = ToolCall(
        tool_call_id=_uuid(),
        tool_name="tests.echo",
        args={"message": "one"},
        permission_scope="debug.echo",
        idempotency_key="k1",
        status=ToolCallStatus.PENDING,
        created_at_ms=10,
    )
    tool_call_2 = ToolCall(
        tool_call_id=_uuid(),
        tool_name="tests.echo",
        args={"message": "two"},
        permission_scope="debug.echo",
        idempotency_key="k2",
        status=ToolCallStatus.RUNNING,
        created_at_ms=20,
    )
    tool_call_3 = ToolCall(
        tool_call_id=_uuid(),
        tool_name="tests.echo",
        args={"message": "three"},
        permission_scope="debug.echo",
        idempotency_key="k3",
        status=ToolCallStatus.SUCCEEDED,
        created_at_ms=30,
    )

    task_1 = Task(
        task_id=task_id_1,
        run_id=run_id_1,
        name="task-1",
        status=TaskStatus.PENDING,
        tool_call=tool_call_1,
        attempts=0,
        max_attempts=3,
        timeout_s=60,
        created_at_ms=1_000,
    )
    task_2 = Task(
        task_id=task_id_2,
        run_id=run_id_1,
        name="task-2",
        status=TaskStatus.RUNNING,
        tool_call=tool_call_2,
        attempts=1,
        max_attempts=3,
        timeout_s=60,
        created_at_ms=3_000,
    )
    task_3 = Task(
        task_id=task_id_3,
        run_id=run_id_2,
        name="task-3",
        status=TaskStatus.SUCCEEDED,
        tool_call=tool_call_3,
        attempts=1,
        max_attempts=1,
        timeout_s=30,
        created_at_ms=2_000,
    )

    uow = container.uow_factory()
    async with uow:
        run_repo = container.repos.run_repo(uow.session)
        task_repo = container.repos.task_repo(uow.session)

        await run_repo.create(
            RunRecord(
                run_id=run_id_1,
                parent_run_id=None,
                created_at_ms=1,
                started_at_ms=None,
                completed_at_ms=None,
            )
        )
        await run_repo.create(
            RunRecord(
                run_id=run_id_2,
                parent_run_id=None,
                created_at_ms=2,
                started_at_ms=None,
                completed_at_ms=None,
            )
        )

        await task_repo.create(task_1)
        await task_repo.create(task_2)
        await task_repo.create(task_3)

    return run_id_1, run_id_2, task_id_1, task_id_2, task_id_3


def test_tasks_list_filters_pagination_and_auth(tmp_path: Path) -> None:
    db_path = tmp_path / "reflexor_api_tasks_read.db"
    _create_schema(db_path)

    settings = ReflexorSettings(
        workspace_root=tmp_path,
        enabled_scopes=[],
        database_url=f"sqlite+aiosqlite:///{db_path}",
        admin_api_key="secret",
    )
    container = AppContainer.build(settings=settings)

    run_id_1, run_id_2, task_id_1, task_id_2, task_id_3 = asyncio.run(_seed_tasks(container))

    app = create_app(container=container)
    with TestClient(app) as client:
        assert client.get("/tasks").status_code == 401

        headers = {"X-API-Key": "secret"}

        all_tasks = client.get("/tasks", headers=headers)
        assert all_tasks.status_code == 200
        payload = all_tasks.json()
        assert payload["total"] == 3
        assert [item["task_id"] for item in payload["items"]] == [task_id_2, task_id_3, task_id_1]
        assert all("tool_call" not in item for item in payload["items"])
        assert all("args" not in item for item in payload["items"])

        paged = client.get("/tasks", headers=headers, params={"limit": 1, "offset": 1})
        assert paged.status_code == 200
        assert [item["task_id"] for item in paged.json()["items"]] == [task_id_3]

        by_run = client.get("/tasks", headers=headers, params={"run_id": run_id_1})
        assert by_run.status_code == 200
        assert [item["task_id"] for item in by_run.json()["items"]] == [task_id_2, task_id_1]

        by_status = client.get("/tasks", headers=headers, params={"status": "running"})
        assert by_status.status_code == 200
        assert [item["task_id"] for item in by_status.json()["items"]] == [task_id_2]

        combined = client.get(
            "/tasks",
            headers=headers,
            params={"run_id": run_id_2, "status": "succeeded"},
        )
        assert combined.status_code == 200
        assert [item["task_id"] for item in combined.json()["items"]] == [task_id_3]

from __future__ import annotations

import json

from typer.testing import CliRunner

from reflexor.cli.container import CliContainer
from reflexor.cli.main import app
from reflexor.config import ReflexorSettings
from reflexor.domain.enums import TaskStatus


class _FakeTasksClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def list_tasks(
        self,
        *,
        limit: int,
        offset: int,
        run_id: str | None = None,
        status: TaskStatus | None = None,
    ) -> dict[str, object]:
        self.calls.append(
            {
                "limit": limit,
                "offset": offset,
                "run_id": run_id,
                "status": status,
            }
        )
        return {
            "limit": limit,
            "offset": offset,
            "total": 1,
            "items": [
                {
                    "task_id": "t1",
                    "run_id": run_id or "r1",
                    "name": "task",
                    "status": "queued",
                    "attempts": 1,
                    "max_attempts": 3,
                    "timeout_s": 60,
                    "depends_on": [],
                    "tool_call_id": "tc1",
                    "tool_name": "mock.echo",
                    "permission_scope": "debug.echo",
                    "idempotency_key": "k1",
                    "tool_call_status": "pending",
                }
            ],
        }


def test_tasks_list_passes_filters_to_client_and_outputs_json() -> None:
    client = _FakeTasksClient()
    container = CliContainer.build(settings=ReflexorSettings(), client=client)  # type: ignore[arg-type]

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "tasks",
            "list",
            "--limit",
            "2",
            "--offset",
            "3",
            "--run-id",
            "r123",
            "--status",
            "queued",
            "--json",
        ],
        obj=container,
    )

    assert result.exit_code == 0
    assert client.calls == [
        {"limit": 2, "offset": 3, "run_id": "r123", "status": TaskStatus.QUEUED}
    ]

    data = json.loads(result.output)
    assert data["limit"] == 2
    assert data["offset"] == 3
    assert data["total"] == 1
    assert data["items"][0]["run_id"] == "r123"


def test_tasks_list_text_output_includes_run_id_and_tool_name() -> None:
    client = _FakeTasksClient()
    container = CliContainer.build(settings=ReflexorSettings(), client=client)  # type: ignore[arg-type]

    runner = CliRunner()
    result = runner.invoke(app, ["tasks", "list"], obj=container)

    assert result.exit_code == 0
    assert "TASK_ID" in result.output
    assert "RUN_ID" in result.output
    assert "mock.echo" in result.output


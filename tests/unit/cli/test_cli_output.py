from __future__ import annotations

import json

from typer.testing import CliRunner

from reflexor.cli.container import CliContainer
from reflexor.cli.main import app
from reflexor.config import ReflexorSettings


class FakeClient:
    async def list_runs(self, *, limit: int, offset: int, status=None, since_ms=None):  # type: ignore[no-untyped-def]
        _ = (status, since_ms)
        return {
            "limit": limit,
            "offset": offset,
            "total": 1,
            "items": [
                {
                    "run_id": "run-1",
                    "created_at_ms": 123,
                    "started_at_ms": None,
                    "completed_at_ms": None,
                    "status": "created",
                    "event_type": "webhook",
                    "event_source": "tests",
                    "tasks_total": 0,
                    "tasks_pending": 0,
                    "tasks_queued": 0,
                    "tasks_running": 0,
                    "tasks_succeeded": 0,
                    "tasks_failed": 0,
                    "tasks_canceled": 0,
                    "approvals_total": 0,
                    "approvals_pending": 0,
                }
            ],
        }


def _container_with_fake_client() -> CliContainer:
    return CliContainer.build(settings=ReflexorSettings(), client=FakeClient())  # type: ignore[arg-type]


def test_runs_list_text_output_includes_headers_and_row() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["runs", "list"], obj=_container_with_fake_client())

    assert result.exit_code == 0
    assert "RUN_ID" in result.output
    assert "STATUS" in result.output
    assert "run-1" in result.output


def test_runs_list_json_output_is_valid_and_has_expected_keys() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--json", "runs", "list"], obj=_container_with_fake_client())

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["limit"] == 50
    assert payload["offset"] == 0
    assert payload["total"] == 1
    assert isinstance(payload["items"], list)
    assert payload["items"][0]["run_id"] == "run-1"


from __future__ import annotations

import json

from typer.testing import CliRunner

from reflexor.cli.container import CliContainer
from reflexor.cli.main import app
from reflexor.config import ReflexorSettings


class _FakeSuppressionsClient:
    def __init__(self) -> None:
        self.list_calls: list[dict[str, object]] = []
        self.clear_calls: list[dict[str, object]] = []

    async def list_suppressions(self, *, limit: int, offset: int) -> dict[str, object]:
        self.list_calls.append({"limit": limit, "offset": offset})
        return {
            "limit": limit,
            "offset": offset,
            "total": 1,
            "items": [
                {
                    "signature_hash": "sig-1",
                    "event_type": "webhook",
                    "event_source": "tests",
                    "signature": {"ticket": "T-1"},
                    "count": 3,
                    "threshold": 2,
                    "window_ms": 60_000,
                    "window_start_ms": 1_000,
                    "suppressed_until_ms": 31_000,
                    "expires_at_ms": 31_000,
                    "resume_required": False,
                    "created_at_ms": 1_000,
                    "updated_at_ms": 1_500,
                }
            ],
        }

    async def clear_suppression(
        self,
        signature_hash: str,
        *,
        cleared_by: str | None = None,
    ) -> dict[str, object]:
        self.clear_calls.append(
            {"signature_hash": signature_hash, "cleared_by": cleared_by}
        )
        return {
            "ok": True,
            "signature_hash": signature_hash,
            "cleared_at_ms": 2_000,
            "cleared_by": cleared_by,
        }


def test_suppressions_list_and_clear_call_client_and_return_json_status() -> None:
    client = _FakeSuppressionsClient()
    container = CliContainer.build(
        settings=ReflexorSettings(profile="dev"),
        client=client,  # type: ignore[arg-type]
    )

    runner = CliRunner()
    listed = runner.invoke(app, ["suppressions", "list", "--json"], obj=container)
    assert listed.exit_code == 0
    assert client.list_calls == [{"limit": 50, "offset": 0}]
    listed_payload = json.loads(listed.output)
    assert listed_payload["total"] == 1
    assert listed_payload["items"][0]["signature_hash"] == "sig-1"

    cleared = runner.invoke(
        app,
        ["suppressions", "clear", "sig-1", "--by", "alice", "--json"],
        obj=container,
    )
    assert cleared.exit_code == 0
    assert client.clear_calls == [{"signature_hash": "sig-1", "cleared_by": "alice"}]
    cleared_payload = json.loads(cleared.output)
    assert cleared_payload["ok"] is True
    assert cleared_payload["signature_hash"] == "sig-1"


def test_suppressions_clear_requires_yes_in_prod() -> None:
    client = _FakeSuppressionsClient()
    container = CliContainer.build(
        settings=ReflexorSettings(profile="prod"),
        client=client,  # type: ignore[arg-type]
    )

    runner = CliRunner()
    result = runner.invoke(app, ["suppressions", "clear", "sig-1", "--json"], obj=container)

    assert result.exit_code != 0
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["error_code"] == "confirmation_required"
    assert client.clear_calls == []


def test_suppressions_clear_proceeds_with_yes_in_prod() -> None:
    client = _FakeSuppressionsClient()
    container = CliContainer.build(
        settings=ReflexorSettings(profile="prod"),
        client=client,  # type: ignore[arg-type]
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["--yes", "suppressions", "clear", "sig-1", "--json"],
        obj=container,
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["signature_hash"] == "sig-1"
    assert client.clear_calls == [{"signature_hash": "sig-1", "cleared_by": None}]

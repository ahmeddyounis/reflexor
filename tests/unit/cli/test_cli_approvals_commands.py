from __future__ import annotations

import json

from typer.testing import CliRunner

from reflexor.cli.container import CliContainer
from reflexor.cli.main import app
from reflexor.config import ReflexorSettings
from reflexor.domain.enums import ApprovalStatus


class _FakeApprovalsClient:
    def __init__(self) -> None:
        self.list_calls: list[dict[str, object]] = []
        self.approve_calls: list[dict[str, object]] = []
        self.deny_calls: list[dict[str, object]] = []

    async def list_approvals(
        self,
        *,
        limit: int,
        offset: int,
        status: ApprovalStatus | None = None,
        run_id: str | None = None,
    ) -> dict[str, object]:
        self.list_calls.append(
            {"limit": limit, "offset": offset, "status": status, "run_id": run_id}
        )
        if offset > 0:
            return {"limit": limit, "offset": offset, "total": 3, "items": []}

        items = [
            {
                "approval_id": "a1",
                "run_id": run_id or "r1",
                "task_id": "t1",
                "tool_call_id": "tc1",
                "status": "pending",
                "created_at_ms": 0,
                "decided_at_ms": None,
                "decided_by": None,
                "payload_hash": "h",
                "preview": "permission_scope: fs.read",
            },
            {
                "approval_id": "a2",
                "run_id": run_id or "r1",
                "task_id": "t2",
                "tool_call_id": "tc2",
                "status": "pending",
                "created_at_ms": 0,
                "decided_at_ms": None,
                "decided_by": None,
                "payload_hash": "h",
                "preview": "permission_scope: fs.write",
            },
            {
                "approval_id": "a3",
                "run_id": run_id or "r1",
                "task_id": "t3",
                "tool_call_id": "tc3",
                "status": "approved",
                "created_at_ms": 0,
                "decided_at_ms": 1,
                "decided_by": "me",
                "payload_hash": "h",
                "preview": "permission_scope: fs.write",
            },
        ]

        if status is not None:
            items = [item for item in items if item.get("status") == str(status)]

        return {
            "limit": limit,
            "offset": offset,
            "total": len(items),
            "items": items[:limit],
        }

    async def approve(
        self, approval_id: str, *, decided_by: str | None = None
    ) -> dict[str, object]:
        self.approve_calls.append({"approval_id": approval_id, "decided_by": decided_by})
        return {"approval": {"approval_id": approval_id, "status": "approved"}}

    async def deny(self, approval_id: str, *, decided_by: str | None = None) -> dict[str, object]:
        self.deny_calls.append({"approval_id": approval_id, "decided_by": decided_by})
        return {"approval": {"approval_id": approval_id, "status": "denied"}}


def test_approvals_approve_and_deny_call_client_and_return_json_status() -> None:
    client = _FakeApprovalsClient()
    container = CliContainer.build(
        settings=ReflexorSettings(profile="dev"),
        client=client,  # type: ignore[arg-type]
    )

    runner = CliRunner()
    approved = runner.invoke(
        app,
        ["approvals", "approve", "a1", "--decided-by", "alice", "--json"],
        obj=container,
    )
    assert approved.exit_code == 0
    assert client.approve_calls == [{"approval_id": "a1", "decided_by": "alice"}]
    approved_payload = json.loads(approved.output)
    assert approved_payload["approval"]["status"] == "approved"

    denied = runner.invoke(
        app,
        ["approvals", "deny", "a2", "--decided-by", "bob", "--json"],
        obj=container,
    )
    assert denied.exit_code == 0
    assert client.deny_calls == [{"approval_id": "a2", "decided_by": "bob"}]
    denied_payload = json.loads(denied.output)
    assert denied_payload["approval"]["status"] == "denied"


def test_approvals_approve_requires_yes_in_prod() -> None:
    client = _FakeApprovalsClient()
    container = CliContainer.build(settings=ReflexorSettings(profile="prod"), client=client)  # type: ignore[arg-type]

    runner = CliRunner()
    result = runner.invoke(app, ["approvals", "approve", "a1", "--json"], obj=container)

    assert result.exit_code != 0
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["error_code"] == "confirmation_required"
    assert client.approve_calls == []


def test_approvals_approve_proceeds_with_yes_in_prod() -> None:
    client = _FakeApprovalsClient()
    container = CliContainer.build(settings=ReflexorSettings(profile="prod"), client=client)  # type: ignore[arg-type]

    runner = CliRunner()
    result = runner.invoke(app, ["--yes", "approvals", "approve", "a1", "--json"], obj=container)

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["approval"]["status"] == "approved"
    assert client.approve_calls == [{"approval_id": "a1", "decided_by": None}]


def test_approvals_list_filters_pending_only_and_scope() -> None:
    client = _FakeApprovalsClient()
    container = CliContainer.build(
        settings=ReflexorSettings(profile="dev"),
        client=client,  # type: ignore[arg-type]
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "approvals",
            "list",
            "--pending-only",
            "--run-id",
            "r123",
            "--scope",
            "fs.write",
            "--json",
        ],
        obj=container,
    )

    assert result.exit_code == 0
    assert client.list_calls[0]["status"] == ApprovalStatus.PENDING
    payload = json.loads(result.output)
    assert payload["total"] == 1
    assert [item["approval_id"] for item in payload["items"]] == ["a2"]


def test_approvals_list_scope_filter_paginates_matches_without_losing_total() -> None:
    class _PagedApprovalsClient(_FakeApprovalsClient):
        async def list_approvals(
            self,
            *,
            limit: int,
            offset: int,
            status: ApprovalStatus | None = None,
            run_id: str | None = None,
        ) -> dict[str, object]:
            self.list_calls.append(
                {"limit": limit, "offset": offset, "status": status, "run_id": run_id}
            )
            items = [
                {
                    "approval_id": f"a{i}",
                    "run_id": run_id or "r1",
                    "task_id": f"t{i}",
                    "tool_call_id": f"tc{i}",
                    "status": "pending",
                    "created_at_ms": i,
                    "decided_at_ms": None,
                    "decided_by": None,
                    "payload_hash": "h",
                    "preview": (
                        "permission_scope: fs.write"
                        if i % 2 == 0
                        else "permission_scope: fs.read"
                    ),
                }
                for i in range(205)
            ]
            if status is not None:
                items = [item for item in items if item.get("status") == str(status)]
            return {
                "limit": limit,
                "offset": offset,
                "total": len(items),
                "items": items[offset : offset + limit],
            }

    client = _PagedApprovalsClient()
    container = CliContainer.build(
        settings=ReflexorSettings(profile="dev"),
        client=client,  # type: ignore[arg-type]
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "approvals",
            "list",
            "--scope",
            "fs.write",
            "--limit",
            "2",
            "--offset",
            "100",
            "--json",
        ],
        obj=container,
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["total"] == 103
    assert [item["approval_id"] for item in payload["items"]] == ["a200", "a202"]
    assert len(client.list_calls) == 2

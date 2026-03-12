from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from reflexor.api.app import create_app
from reflexor.bootstrap.container import AppContainer
from reflexor.config import ReflexorSettings
from reflexor.domain.enums import ApprovalStatus, TaskStatus, ToolCallStatus
from reflexor.domain.models import Approval, Task, ToolCall
from reflexor.infra.db.models import Base
from reflexor.orchestrator.queue import Lease, Queue, TaskEnvelope
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


@dataclass(slots=True)
class _RecordingQueue(Queue):
    enqueued: list[TaskEnvelope] = field(default_factory=list)

    async def enqueue(self, envelope: TaskEnvelope) -> None:
        self.enqueued.append(envelope)

    async def dequeue(
        self,
        timeout_s: float | None = None,
        *,
        wait_s: float | None = 0.0,
    ) -> Lease | None:
        _ = (timeout_s, wait_s)
        return None

    async def ack(self, lease: Lease) -> None:
        _ = lease

    async def nack(
        self, lease: Lease, delay_s: float | None = None, reason: str | None = None
    ) -> None:
        _ = (lease, delay_s, reason)

    async def aclose(self) -> None:
        return


@dataclass(slots=True)
class _FailFirstEnqueueQueue(_RecordingQueue):
    enqueue_attempts: int = 0

    async def enqueue(self, envelope: TaskEnvelope) -> None:
        self.enqueue_attempts += 1
        if self.enqueue_attempts == 1:
            raise RuntimeError("queue unavailable")
        await super().enqueue(envelope)


async def _seed(container: AppContainer) -> dict[str, str]:
    run_id_1 = _uuid()
    run_id_2 = _uuid()

    approval_id_approve = _uuid()
    approval_id_existing_approved = _uuid()
    approval_id_deny = _uuid()

    tool_call_1 = ToolCall(
        tool_call_id=_uuid(),
        tool_name="tests.tool",
        args={"k": "v"},
        permission_scope="debug.echo",
        idempotency_key="k-1",
        status=ToolCallStatus.PENDING,
        created_at_ms=1,
    )
    tool_call_2 = ToolCall(
        tool_call_id=_uuid(),
        tool_name="tests.tool",
        args={"k": "v2"},
        permission_scope="debug.echo",
        idempotency_key="k-2",
        status=ToolCallStatus.PENDING,
        created_at_ms=2,
    )
    tool_call_3 = ToolCall(
        tool_call_id=_uuid(),
        tool_name="tests.tool",
        args={"k": "v3"},
        permission_scope="debug.echo",
        idempotency_key="k-3",
        status=ToolCallStatus.PENDING,
        created_at_ms=3,
    )

    task_approve = Task(
        task_id=_uuid(),
        run_id=run_id_1,
        name="needs-approval",
        status=TaskStatus.WAITING_APPROVAL,
        tool_call=tool_call_1,
        attempts=0,
        max_attempts=3,
        timeout_s=60,
        created_at_ms=1_000,
    )
    task_existing_approved = Task(
        task_id=_uuid(),
        run_id=run_id_1,
        name="already-approved",
        status=TaskStatus.QUEUED,
        tool_call=tool_call_2,
        attempts=0,
        max_attempts=3,
        timeout_s=60,
        created_at_ms=2_000,
    )
    task_deny = Task(
        task_id=_uuid(),
        run_id=run_id_2,
        name="deny-this",
        status=TaskStatus.WAITING_APPROVAL,
        tool_call=tool_call_3,
        attempts=0,
        max_attempts=3,
        timeout_s=60,
        created_at_ms=3_000,
    )

    approval_to_approve = Approval(
        approval_id=approval_id_approve,
        run_id=run_id_1,
        task_id=task_approve.task_id,
        tool_call_id=tool_call_1.tool_call_id,
        status=ApprovalStatus.PENDING,
        created_at_ms=10,
        preview="approve me",
        payload_hash="hash-approve",
    )
    approval_approved = Approval(
        approval_id=approval_id_existing_approved,
        run_id=run_id_1,
        task_id=task_existing_approved.task_id,
        tool_call_id=tool_call_2.tool_call_id,
        status=ApprovalStatus.APPROVED,
        created_at_ms=20,
        decided_at_ms=21,
        decided_by="tester",
        preview="approved",
        payload_hash="hash-approved",
    )
    approval_to_deny = Approval(
        approval_id=approval_id_deny,
        run_id=run_id_2,
        task_id=task_deny.task_id,
        tool_call_id=tool_call_3.tool_call_id,
        status=ApprovalStatus.PENDING,
        created_at_ms=30,
        preview="deny me",
        payload_hash="hash-deny",
    )

    uow = container.uow_factory()
    async with uow:
        run_repo = container.repos.run_repo(uow.session)
        task_repo = container.repos.task_repo(uow.session)
        approval_repo = container.repos.approval_repo(uow.session)

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

        await task_repo.create(task_approve)
        await task_repo.create(task_existing_approved)
        await task_repo.create(task_deny)

        await approval_repo.create(approval_to_approve)
        await approval_repo.create(approval_approved)
        await approval_repo.create(approval_to_deny)

    return {
        "run_id_1": run_id_1,
        "run_id_2": run_id_2,
        "approval_id_approve": approval_id_approve,
        "approval_id_existing_approved": approval_id_existing_approved,
        "approval_id_deny": approval_id_deny,
        "task_id_approve": task_approve.task_id,
        "task_id_existing_approved": task_existing_approved.task_id,
        "task_id_deny": task_deny.task_id,
        "tool_call_id_deny": tool_call_3.tool_call_id,
    }


async def _assert_db_state(container: AppContainer, seeded: dict[str, str]) -> None:
    uow = container.uow_factory()
    async with uow:
        task_repo = container.repos.task_repo(uow.session)
        tool_call_repo = container.repos.tool_call_repo(uow.session)

        approved_task = await task_repo.get(seeded["task_id_approve"])
        assert approved_task is not None
        assert approved_task.status == TaskStatus.QUEUED

        existing_approved_task = await task_repo.get(seeded["task_id_existing_approved"])
        assert existing_approved_task is not None
        assert existing_approved_task.status == TaskStatus.QUEUED

        denied_task = await task_repo.get(seeded["task_id_deny"])
        assert denied_task is not None
        assert denied_task.status == TaskStatus.CANCELED

        denied_tool_call = await tool_call_repo.get(seeded["tool_call_id_deny"])
        assert denied_tool_call is not None
        assert denied_tool_call.status == ToolCallStatus.DENIED


def test_approvals_list_approve_and_deny_are_idempotent_and_requeue(tmp_path: Path) -> None:
    db_path = tmp_path / "reflexor_api_approvals_workflow.db"
    _create_schema(db_path)

    queue = _RecordingQueue()
    settings = ReflexorSettings(
        workspace_root=tmp_path,
        enabled_scopes=[],
        database_url=f"sqlite+aiosqlite:///{db_path}",
        admin_api_key="secret",
    )
    container = AppContainer.build(settings=settings, queue=queue)
    seeded = asyncio.run(_seed(container))

    app = create_app(container=container)
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/approvals").status_code == 401

        headers = {"X-API-Key": "secret"}

        listed = client.get("/approvals", headers=headers)
        assert listed.status_code == 200
        payload = listed.json()
        assert payload["total"] == 3
        assert [item["status"] for item in payload["items"]] == [
            ApprovalStatus.PENDING.value,
            ApprovalStatus.PENDING.value,
            ApprovalStatus.APPROVED.value,
        ]

        filtered = client.get(
            "/approvals",
            headers=headers,
            params={"run_id": seeded["run_id_2"], "status": "pending"},
        )
        assert filtered.status_code == 200
        filtered_payload = filtered.json()
        assert filtered_payload["total"] == 1
        assert [item["approval_id"] for item in filtered_payload["items"]] == [
            seeded["approval_id_deny"]
        ]

        approved = client.post(
            f"/approvals/{seeded['approval_id_approve']}/approve",
            headers=headers,
            json={"decided_by": "operator"},
        )
        assert approved.status_code == 200
        assert approved.json()["approval"]["status"] == ApprovalStatus.APPROVED.value
        assert len(queue.enqueued) == 1
        assert queue.enqueued[0].task_id == seeded["task_id_approve"]

        approved_again = client.post(
            f"/approvals/{seeded['approval_id_approve']}/approve",
            headers=headers,
            json={"decided_by": "operator"},
        )
        assert approved_again.status_code == 200
        assert approved_again.json()["approval"]["status"] == ApprovalStatus.APPROVED.value
        assert len(queue.enqueued) == 1, "approve should be idempotent (no duplicate enqueue)"

        deny_approved = client.post(
            f"/approvals/{seeded['approval_id_existing_approved']}/deny",
            headers=headers,
            json={"decided_by": "operator"},
        )
        assert deny_approved.status_code == 400
        assert deny_approved.json()["message"] == "approved approval cannot be denied"
        assert len(queue.enqueued) == 1

        denied = client.post(
            f"/approvals/{seeded['approval_id_deny']}/deny",
            headers=headers,
            json={"decided_by": "operator"},
        )
        assert denied.status_code == 200
        assert denied.json()["approval"]["status"] == ApprovalStatus.DENIED.value
        assert len(queue.enqueued) == 1, "deny should not enqueue"

        denied_again = client.post(
            f"/approvals/{seeded['approval_id_deny']}/deny",
            headers=headers,
            json={"decided_by": "operator"},
        )
        assert denied_again.status_code == 200
        assert denied_again.json()["approval"]["status"] == ApprovalStatus.DENIED.value
        assert len(queue.enqueued) == 1, "deny should be idempotent"

        approve_denied = client.post(
            f"/approvals/{seeded['approval_id_deny']}/approve",
            headers=headers,
            json={"decided_by": "operator"},
        )
        assert approve_denied.status_code == 400
        assert approve_denied.json()["message"] == "denied approval cannot be approved"
        assert len(queue.enqueued) == 1

    asyncio.run(_assert_db_state(container, seeded))


def test_approve_recovers_waiting_task_when_requeue_fails(tmp_path: Path) -> None:
    db_path = tmp_path / "reflexor_api_approvals_requeue_recovery.db"
    _create_schema(db_path)

    queue = _FailFirstEnqueueQueue()
    settings = ReflexorSettings(
        workspace_root=tmp_path,
        enabled_scopes=[],
        database_url=f"sqlite+aiosqlite:///{db_path}",
        admin_api_key="secret",
    )
    container = AppContainer.build(settings=settings, queue=queue)
    seeded = asyncio.run(_seed(container))

    app = create_app(container=container)
    with TestClient(app, raise_server_exceptions=False) as client:
        headers = {"X-API-Key": "secret"}

        failed = client.post(
            f"/approvals/{seeded['approval_id_approve']}/approve",
            headers=headers,
            json={"decided_by": "operator"},
        )
        assert failed.status_code == 500
        assert failed.json()["error_code"] == "internal_error"
        assert queue.enqueued == []

        async def _assert_recovered_waiting_state() -> None:
            uow = container.uow_factory()
            async with uow:
                approval_repo = container.repos.approval_repo(uow.session)
                task_repo = container.repos.task_repo(uow.session)

                approval = await approval_repo.get(seeded["approval_id_approve"])
                assert approval is not None
                assert approval.status == ApprovalStatus.APPROVED

                task = await task_repo.get(seeded["task_id_approve"])
                assert task is not None
                assert task.status == TaskStatus.WAITING_APPROVAL

        asyncio.run(_assert_recovered_waiting_state())

        retried = client.post(
            f"/approvals/{seeded['approval_id_approve']}/approve",
            headers=headers,
            json={"decided_by": "operator"},
        )
        assert retried.status_code == 200
        assert retried.json()["approval"]["status"] == ApprovalStatus.APPROVED.value
        assert len(queue.enqueued) == 1
        assert queue.enqueued[0].task_id == seeded["task_id_approve"]

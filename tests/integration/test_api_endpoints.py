from __future__ import annotations

from pathlib import Path
from typing import cast

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession

from reflexor.api.app import create_app
from reflexor.api.container import AppContainer
from reflexor.config import ReflexorSettings
from reflexor.executor.concurrency import ConcurrencyLimiter
from reflexor.executor.idempotency import IdempotencyLedger
from reflexor.executor.retries import RetryPolicy
from reflexor.executor.service import ExecutionDisposition, ExecutorRepoFactory, ExecutorService
from reflexor.infra.db.models import Base
from reflexor.infra.db.repos import SqlAlchemyIdempotencyLedger
from reflexor.infra.queue.in_memory_queue import InMemoryQueue
from reflexor.orchestrator.reflex_rules import RuleBasedReflexRouter
from reflexor.storage.uow import DatabaseSession
from reflexor.tools.mock_tool import MockTool
from reflexor.tools.registry import ToolRegistry


def _create_schema(db_path: Path) -> None:
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    engine.dispose()


def _build_executor(container: AppContainer) -> ExecutorService:
    repos = ExecutorRepoFactory(
        task_repo=container.repos.task_repo,
        tool_call_repo=container.repos.tool_call_repo,
        approval_repo=container.repos.approval_repo,
        run_packet_repo=container.repos.run_packet_repo,
    )

    def ledger_factory(session: DatabaseSession) -> IdempotencyLedger:
        return SqlAlchemyIdempotencyLedger(
            cast(AsyncSession, session),
            settings=container.settings,
        )

    return ExecutorService(
        uow_factory=container.uow_factory,
        repos=repos,
        queue=container.queue,
        policy_runner=container.policy_runner,
        tool_registry=container.tool_registry,
        idempotency_ledger=ledger_factory,
        retry_policy=RetryPolicy(max_attempts=1, base_delay_s=1.0, max_delay_s=10.0, jitter=0.0),
        limiter=ConcurrencyLimiter(max_global=10),
        clock=container.orchestrator_engine.clock,
    )


@pytest.mark.asyncio
async def test_api_endpoints_asgi_end_to_end(tmp_path: Path) -> None:
    db_path = tmp_path / "reflexor_api_endpoints.db"
    _create_schema(db_path)

    tool = MockTool(
        tool_name="tests.api.approval_tool",
        permission_scope="fs.write",
        side_effects=True,
    )
    registry = ToolRegistry()
    registry.register(tool)

    router = RuleBasedReflexRouter.from_raw_rules(
        [
            {
                "rule_id": "approval_flow",
                "match": {"event_type": "webhook"},
                "action": {
                    "kind": "fast_tool",
                    "tool_name": tool.manifest.name,
                    "args_template": {"k": "v"},
                },
            }
        ]
    )

    settings = ReflexorSettings(
        workspace_root=tmp_path,
        enabled_scopes=["fs.write"],
        approval_required_scopes=["fs.write"],
        database_url=f"sqlite+aiosqlite:///{db_path}",
        admin_api_key="secret",
    )

    container = AppContainer.build(settings=settings, tool_registry=registry, reflex_router=router)
    assert isinstance(container.queue, InMemoryQueue)

    executor = _build_executor(container)
    app = create_app(container=container)

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=True)
    admin_headers = {"X-API-Key": "secret"}

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            event_body = {
                "type": "webhook",
                "source": "tests",
                "payload": {"n": 1},
                "dedupe_key": "ticket:T-1",
                "received_at_ms": 123,
            }

            first = await client.post("/events", json=event_body)
            assert first.status_code == 202
            first_payload = first.json()
            assert first_payload["ok"] is True
            assert first_payload["duplicate"] is False
            event_id = str(first_payload["event_id"])
            run_id = str(first_payload["run_id"])
            assert event_id
            assert run_id

            second = await client.post("/events", json={**event_body, "payload": {"n": 2}})
            assert second.status_code == 200
            second_payload = second.json()
            assert second_payload["ok"] is True
            assert second_payload["duplicate"] is True
            assert second_payload["event_id"] == event_id
            assert second_payload["run_id"] == run_id

            unauthorized = await client.get("/runs")
            assert unauthorized.status_code == 401
            assert unauthorized.json()["error_code"] == "unauthorized"

            runs = await client.get("/runs", headers=admin_headers)
            assert runs.status_code == 200
            runs_payload = runs.json()
            assert runs_payload["total"] == 1
            assert [item["run_id"] for item in runs_payload["items"]] == [run_id]

            run_detail = await client.get(f"/runs/{run_id}", headers=admin_headers)
            assert run_detail.status_code == 200
            run_detail_payload = run_detail.json()
            assert run_detail_payload["summary"]["run_id"] == run_id
            assert isinstance(run_detail_payload["run_packet"], dict)

            tasks = await client.get("/tasks", headers=admin_headers, params={"run_id": run_id})
            assert tasks.status_code == 200
            tasks_payload = tasks.json()
            assert tasks_payload["total"] == 1
            task_id = str(tasks_payload["items"][0]["task_id"])
            assert task_id
            assert tasks_payload["items"][0]["run_id"] == run_id

            tasks_other = await client.get(
                "/tasks", headers=admin_headers, params={"run_id": "not-a-run"}
            )
            assert tasks_other.status_code == 200
            assert tasks_other.json()["total"] == 0

            lease = await container.queue.dequeue(wait_s=0.0)
            assert lease is not None
            report = await executor.process_lease(lease)
            assert report.disposition == ExecutionDisposition.WAITING_APPROVAL
            assert tool.invocations == []
            assert report.approval_id is not None

            approvals = await client.get("/approvals", headers=admin_headers)
            assert approvals.status_code == 200
            approvals_payload = approvals.json()
            assert approvals_payload["total"] == 1
            approval_id = str(approvals_payload["items"][0]["approval_id"])
            assert approval_id
            assert approvals_payload["items"][0]["status"] == "pending"
            assert approval_id == report.approval_id

            approved = await client.post(
                f"/approvals/{approval_id}/approve",
                headers=admin_headers,
                json={"decided_by": "operator"},
            )
            assert approved.status_code == 200
            assert approved.json()["approval"]["status"] == "approved"

            lease_after_approve = await container.queue.dequeue(wait_s=0.0)
            assert lease_after_approve is not None
            succeeded = await executor.process_lease(lease_after_approve)
            assert succeeded.disposition == ExecutionDisposition.SUCCEEDED
            assert len(tool.invocations) == 1

            tasks_after = await client.get(
                "/tasks", headers=admin_headers, params={"run_id": run_id}
            )
            assert tasks_after.status_code == 200
            assert tasks_after.json()["items"][0]["status"] == "succeeded"

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import cast

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession
from typer.testing import CliRunner

from reflexor.bootstrap.container import AppContainer
from reflexor.cli.client import LocalClient
from reflexor.cli.container import CliContainer
from reflexor.cli.main import app
from reflexor.config import ReflexorSettings
from reflexor.executor.concurrency import ConcurrencyLimiter
from reflexor.executor.retries import RetryPolicy
from reflexor.executor.service import ExecutionDisposition, ExecutorRepoFactory, ExecutorService
from reflexor.infra.db.models import Base
from reflexor.infra.db.repos import SqlAlchemyIdempotencyLedger
from reflexor.infra.queue.in_memory_queue import InMemoryQueue
from reflexor.orchestrator.reflex_rules import RuleBasedReflexRouter
from reflexor.storage.idempotency import IdempotencyLedger
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


def test_cli_smoke_help() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Reflexor CLI" in result.output
    assert "submit-event" in result.output


def test_cli_smoke_local_submit_event_and_approval_flow(tmp_path: Path) -> None:
    db_path = tmp_path / "reflexor_cli_smoke.db"
    _create_schema(db_path)

    tool = MockTool(
        tool_name="tests.cli.approval_tool",
        permission_scope="fs.write",
        side_effects=True,
        idempotent=True,
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

    queue = InMemoryQueue()

    settings = ReflexorSettings(
        profile="dev",
        workspace_root=tmp_path,
        enabled_scopes=["fs.write"],
        approval_required_scopes=["fs.write"],
        database_url=f"sqlite+aiosqlite:///{db_path}",
    )

    container = AppContainer.build(
        settings=settings,
        tool_registry=registry,
        reflex_router=router,
        queue=queue,
    )

    try:
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

        executor = _build_executor(container)

        runner = CliRunner()

        submitted = runner.invoke(
            app,
            ["submit-event", "--type", "webhook", "--source", "tests", "--payload", "{}", "--json"],
            obj=cli_container,
        )
        assert submitted.exit_code == 0
        submitted_payload = json.loads(submitted.output)
        run_id = str(submitted_payload["run_id"])
        assert run_id

        runs = runner.invoke(app, ["runs", "list", "--json"], obj=cli_container)
        assert runs.exit_code == 0
        runs_payload = json.loads(runs.output)
        assert runs_payload["total"] >= 1
        assert run_id in {item["run_id"] for item in runs_payload["items"]}

        tasks = runner.invoke(
            app,
            ["tasks", "list", "--run-id", run_id, "--json"],
            obj=cli_container,
        )
        assert tasks.exit_code == 0
        tasks_payload = json.loads(tasks.output)
        assert tasks_payload["total"] == 1
        assert tasks_payload["items"][0]["status"] == "queued"

        lease = asyncio.run(container.queue.dequeue(wait_s=0.0))
        assert lease is not None
        report = asyncio.run(executor.process_lease(lease))
        assert report.disposition == ExecutionDisposition.WAITING_APPROVAL
        assert tool.invocations == []
        assert report.approval_id is not None

        tasks_waiting = runner.invoke(
            app,
            ["tasks", "list", "--run-id", run_id, "--status", "waiting_approval", "--json"],
            obj=cli_container,
        )
        assert tasks_waiting.exit_code == 0
        waiting_payload = json.loads(tasks_waiting.output)
        assert waiting_payload["total"] == 1

        approvals = runner.invoke(
            app,
            ["approvals", "list", "--pending-only", "--json"],
            obj=cli_container,
        )
        assert approvals.exit_code == 0
        approvals_payload = json.loads(approvals.output)
        assert approvals_payload["total"] == 1
        approval_id = str(approvals_payload["items"][0]["approval_id"])
        assert approval_id == report.approval_id

        approved = runner.invoke(
            app,
            ["approvals", "approve", approval_id, "--json"],
            obj=cli_container,
        )
        assert approved.exit_code == 0
        approved_payload = json.loads(approved.output)
        assert approved_payload["approval"]["status"] == "approved"

        requeued_tasks = runner.invoke(
            app,
            ["tasks", "list", "--run-id", run_id, "--status", "queued", "--json"],
            obj=cli_container,
        )
        assert requeued_tasks.exit_code == 0
        assert json.loads(requeued_tasks.output)["total"] == 1

        lease_after_approve = asyncio.run(container.queue.dequeue(wait_s=0.0))
        assert lease_after_approve is not None
        assert lease_after_approve.envelope.payload is not None
        assert str(lease_after_approve.envelope.payload.get("approval_id")) == approval_id
        asyncio.run(container.queue.ack(lease_after_approve))
    finally:
        asyncio.run(container.aclose())

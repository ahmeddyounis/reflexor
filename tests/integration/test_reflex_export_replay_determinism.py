from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession

from reflexor.api.container import AppContainer
from reflexor.config import ReflexorSettings
from reflexor.domain.enums import RunStatus
from reflexor.domain.models_event import Event
from reflexor.executor.concurrency import ConcurrencyLimiter
from reflexor.executor.idempotency import IdempotencyLedger
from reflexor.executor.retries import RetryPolicy
from reflexor.executor.service import ExecutionDisposition, ExecutorRepoFactory, ExecutorService
from reflexor.infra.db.models import Base
from reflexor.infra.db.repos import SqlAlchemyIdempotencyLedger
from reflexor.orchestrator.reflex_rules import RuleBasedReflexRouter
from reflexor.replay.exporter import export_run_packet
from reflexor.replay.runner import ReplayMode, ReplayRunner
from reflexor.storage.uow import DatabaseSession
from reflexor.tools.mock_tool import MockTool
from reflexor.tools.registry import ToolRegistry
from reflexor.tools.sdk import ToolResult


def _create_schema(db_path: Path) -> None:
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    engine.dispose()


def _uuid() -> str:
    return str(uuid.uuid4())


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
        metrics=container.metrics,
    )


@pytest.mark.asyncio
async def test_reflex_export_and_replay_is_deterministic_and_safe(tmp_path: Path) -> None:
    secret_token = "sk-super-secret-token-1234567890"
    secret_bearer = f"Bearer {secret_token}"

    event_id = "11111111-1111-4111-8111-111111111111"
    tool_name = "tests.recording"

    db_path = tmp_path / "reflexor_reflex_export_replay.db"
    _create_schema(db_path)

    settings = ReflexorSettings(
        profile="dev",
        dry_run=True,
        workspace_root=tmp_path,
        enabled_scopes=["fs.read"],
        approval_required_scopes=[],
        http_allowed_domains=[],
        webhook_allowed_targets=[],
        database_url=f"sqlite+aiosqlite:///{db_path}",
        max_tool_output_bytes=2_000,
        max_run_packet_bytes=64_000,
    )

    registry = ToolRegistry()
    tool = MockTool(tool_name=tool_name, permission_scope="fs.read", side_effects=False)
    tool.set_static_result(
        {"authorization": secret_bearer, "event_id": event_id},
        ToolResult(ok=True, data={"note": secret_bearer}),
    )
    registry.register(tool)

    router = RuleBasedReflexRouter.from_raw_rules(
        [
            {
                "rule_id": "reflex_mock",
                "match": {"event_type": "webhook"},
                "action": {
                    "kind": "fast_tool",
                    "tool_name": tool_name,
                    "args_template": {
                        "authorization": "${payload.authorization}",
                        "event_id": "${event.event_id}",
                    },
                },
            }
        ]
    )

    container = AppContainer.build(settings=settings, tool_registry=registry, reflex_router=router)
    try:
        event = Event(
            event_id=event_id,
            type="webhook",
            source="tests.reflex_export_replay",
            received_at_ms=1,
            payload={"authorization": secret_bearer},
        )
        submit_outcome = await container.submit_events.submit_event(event)
        run_id = str(submit_outcome.run_id)

        lease = await container.queue.dequeue(wait_s=0.0)
        assert lease is not None, "expected a queued task envelope for reflex fast_tool"

        executor = _build_executor(container)
        report = await executor.process_lease(lease)
        assert report.disposition == ExecutionDisposition.SUCCEEDED

        export_path = tmp_path / "captured_export.json"
        await export_run_packet(run_id, export_path, settings=settings)

        exported_raw = export_path.read_text(encoding="utf-8")
        assert secret_token not in exported_raw, "export must not include raw secret substrings"

        exported = json.loads(exported_raw)
        assert exported["packet"]["run_id"] == run_id
        assert len(exported["packet"]["tool_results"]) == 1

        runner = ReplayRunner(settings=settings)
        outcome1 = await runner.replay_from_file(export_path, mode=ReplayMode.MOCK_TOOLS_RECORDED)
        outcome2 = await runner.replay_from_file(export_path, mode=ReplayMode.MOCK_TOOLS_RECORDED)

        assert outcome1.parent_run_id == run_id
        assert outcome1.tasks_total == 1
        assert outcome1.tool_calls_total == 1
        assert outcome1.tool_invocations_total == 1
        assert outcome1.tool_invocations_by_name == {tool_name: 1}
        assert outcome1.dry_run is True

        assert outcome2.parent_run_id == run_id
        assert outcome2.tasks_total == outcome1.tasks_total
        assert outcome2.tool_calls_total == outcome1.tool_calls_total
        assert outcome2.tool_invocations_total == outcome1.tool_invocations_total
        assert outcome2.tool_invocations_by_name == outcome1.tool_invocations_by_name

        uow = container.uow_factory()
        async with uow:
            session = uow.session
            run_repo = container.repos.run_repo(session)
            run_packet_repo = container.repos.run_packet_repo(session)

            captured_run = await run_repo.get(run_id)
            assert captured_run is not None

            replay_run_1 = await run_repo.get(outcome1.run_id)
            assert replay_run_1 is not None
            assert replay_run_1.parent_run_id == run_id

            replay_packet_1 = await run_packet_repo.get(outcome1.run_id)
            assert replay_packet_1 is not None
            assert replay_packet_1.parent_run_id == run_id
            assert len(replay_packet_1.tool_results) == 1

            replay_run_2 = await run_repo.get(outcome2.run_id)
            assert replay_run_2 is not None
            assert replay_run_2.parent_run_id == run_id

            replay_packet_2 = await run_packet_repo.get(outcome2.run_id)
            assert replay_packet_2 is not None
            assert replay_packet_2.parent_run_id == run_id
            assert len(replay_packet_2.tool_results) == 1

            summary = await container.run_queries.get_run_summary(run_id)
            assert summary is not None
            assert summary.status in {RunStatus.CREATED, RunStatus.SUCCEEDED}
    finally:
        await container.aclose()


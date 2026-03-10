from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine

from reflexor.api.app import create_app
from reflexor.bootstrap.container import AppContainer
from reflexor.config import ReflexorSettings
from reflexor.domain.enums import TaskStatus
from reflexor.infra.db.models import Base
from reflexor.orchestrator.reflex_rules import RuleBasedReflexRouter
from reflexor.tools.impl.echo import EchoTool
from reflexor.tools.registry import ToolRegistry


def _create_schema(db_path: Path) -> None:
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    engine.dispose()


@pytest.mark.asyncio
async def test_api_event_planning_executes_dependency_order_and_updates_memory(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "reflexor_api_planning_execution.db"
    _create_schema(db_path)

    settings = ReflexorSettings(
        workspace_root=tmp_path,
        enabled_scopes=["fs.read"],
        database_url=f"sqlite+aiosqlite:///{db_path}",
        planner_backend="heuristic",
        planner_interval_s=3600,
        planner_debounce_s=3600,
        max_run_packet_bytes=100_000,
    )
    router = RuleBasedReflexRouter.from_raw_rules(
        [
            {
                "rule_id": "plan_webhooks",
                "match": {"event_type": "webhook"},
                "action": {"kind": "needs_planning"},
            }
        ]
    )
    registry = ToolRegistry()
    registry.register(EchoTool())

    container = AppContainer.build(
        settings=settings,
        tool_registry=registry,
        reflex_router=router,
    )
    executor, _ = container.build_executor_service(concurrency=1)
    app = create_app(container=container)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=True)

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            event_response = await client.post(
                "/events",
                json={
                    "type": "webhook",
                    "source": "tests",
                    "payload": {
                        "planner_tasks": [
                            {
                                "name": "fetch",
                                "tool_name": "debug.echo",
                                "args": {"step": "fetch"},
                                "declared_permission_scope": "fs.read",
                            },
                            {
                                "name": "report",
                                "tool_name": "debug.echo",
                                "args": {"step": "report"},
                                "declared_permission_scope": "fs.read",
                                "depends_on": ["fetch"],
                            },
                        ]
                    },
                    "received_at_ms": 123,
                },
            )
            assert event_response.status_code == 202
            event_payload = event_response.json()
            event_run_id = str(event_payload["run_id"])

            planning_run_id = await container.orchestrator_engine.run_planning_once(trigger="event")

            uow = container.uow_factory()
            async with uow:
                task_repo = container.repos.task_repo(uow.session)
                initial_tasks = await task_repo.list_by_run(planning_run_id)
                by_name = {task.name: task for task in initial_tasks}
                assert by_name["fetch"].status == TaskStatus.QUEUED
                assert by_name["report"].status == TaskStatus.PENDING
                assert by_name["report"].depends_on == [by_name["fetch"].task_id]

            first_lease = await container.queue.dequeue(wait_s=0.0)
            assert first_lease is not None
            assert first_lease.envelope.task_id == by_name["fetch"].task_id

            first_report = await executor.process_lease(first_lease)
            assert first_report.disposition.value == "succeeded"

            second_lease = await container.queue.dequeue(wait_s=0.0)
            assert second_lease is not None
            assert second_lease.envelope.task_id == by_name["report"].task_id
            assert second_lease.envelope.trace is not None
            assert second_lease.envelope.trace["reason"] == "dependency_satisfied"
            assert second_lease.envelope.payload is not None
            assert second_lease.envelope.payload["upstream_task_id"] == by_name["fetch"].task_id

            second_report = await executor.process_lease(second_lease)
            assert second_report.disposition.value == "succeeded"
            assert await container.queue.dequeue(wait_s=0.0) is None

            uow = container.uow_factory()
            async with uow:
                task_repo = container.repos.task_repo(uow.session)
                run_packet_repo = container.repos.run_packet_repo(uow.session)
                memory_repo = container.repos.memory_repo(uow.session)

                completed_tasks = await task_repo.list_by_run(planning_run_id)
                completed_by_name = {task.name: task for task in completed_tasks}
                assert completed_by_name["fetch"].status == TaskStatus.SUCCEEDED
                assert completed_by_name["report"].status == TaskStatus.SUCCEEDED

                planning_packet = await run_packet_repo.get(planning_run_id)
                assert planning_packet is not None
                assert len(planning_packet.tool_results) == 2
                assert len(planning_packet.policy_decisions) == 2
                assert {task.name: task.status for task in planning_packet.tasks} == {
                    "fetch": TaskStatus.SUCCEEDED,
                    "report": TaskStatus.SUCCEEDED,
                }

                planning_memory = await memory_repo.get_by_run(planning_run_id)
                assert planning_memory is not None
                counts = planning_memory.content["counts"]
                assert isinstance(counts, dict)
                assert counts["tool_results_total"] == 2
                assert counts["tasks_succeeded"] == 2

                event_memory = await memory_repo.get_by_run(event_run_id)
                assert event_memory is not None
                assert event_memory.event_type == "webhook"

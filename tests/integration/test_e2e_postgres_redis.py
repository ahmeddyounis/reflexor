from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import cast
from uuid import uuid4

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncSession

redis = pytest.importorskip("redis")

from reflexor.api.app import create_app  # noqa: E402
from reflexor.bootstrap.container import AppContainer  # noqa: E402
from reflexor.config import ReflexorSettings  # noqa: E402
from reflexor.domain.enums import TaskStatus, ToolCallStatus  # noqa: E402
from reflexor.executor.concurrency import ConcurrencyLimiter  # noqa: E402
from reflexor.executor.idempotency import IdempotencyLedger  # noqa: E402
from reflexor.executor.retries import RetryPolicy  # noqa: E402
from reflexor.executor.service import ExecutorRepoFactory, ExecutorService  # noqa: E402
from reflexor.infra.db.repos import SqlAlchemyIdempotencyLedger  # noqa: E402
from reflexor.infra.queue.redis_streams import RedisStreamsQueue  # noqa: E402
from reflexor.orchestrator.reflex_rules import RuleBasedReflexRouter  # noqa: E402
from reflexor.storage.uow import DatabaseSession  # noqa: E402
from reflexor.tools.mock_tool import MockTool  # noqa: E402
from reflexor.tools.registry import ToolRegistry  # noqa: E402
from reflexor.worker.runner import WorkerRunner  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _postgres_dsn() -> str:
    dsn = os.environ.get("TEST_POSTGRES_DSN") or os.environ.get("POSTGRES_DSN")
    if not dsn:
        pytest.skip("TEST_POSTGRES_DSN or POSTGRES_DSN is not set")
    return dsn.strip()


def _redis_url() -> str:
    url = os.environ.get("TEST_REDIS_URL") or os.environ.get("REDIS_URL")
    if not url:
        pytest.skip("TEST_REDIS_URL or REDIS_URL is not set")
    return url.strip()


def _alembic_upgrade_head(*, database_url: str) -> None:
    if not database_url.lower().startswith("postgresql+asyncpg"):
        pytest.skip("TEST_POSTGRES_DSN must be a postgresql+asyncpg URL")

    pytest.importorskip("asyncpg")

    cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_REPO_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(cfg, "head")


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
        retry_policy=RetryPolicy(max_attempts=1, base_delay_s=0.01, max_delay_s=0.1, jitter=0.0),
        limiter=ConcurrencyLimiter(max_global=10),
        clock=container.orchestrator_engine.clock,
        metrics=container.metrics,
    )


async def _cleanup_redis(url: str, *, keys: list[str]) -> None:
    client = redis.asyncio.Redis.from_url(url, decode_responses=True)
    try:
        if keys:
            await client.delete(*keys)
    finally:
        await client.aclose(close_connection_pool=True)


async def _run_e2e_flow(*, database_url: str, redis_url: str, workspace_root: Path) -> None:
    tool = MockTool(
        tool_name="tests.e2e.mock_tool",
        permission_scope="fs.read",
        side_effects=False,
    )
    registry = ToolRegistry()
    registry.register(tool)

    router = RuleBasedReflexRouter.from_raw_rules(
        [
            {
                "rule_id": "e2e_reflex",
                "match": {"event_type": "webhook"},
                "action": {
                    "kind": "fast_tool",
                    "tool_name": tool.manifest.name,
                    "args_template": {"k": "v"},
                },
            }
        ]
    )

    stream_key = f"test:reflexor:e2e:stream:{uuid4().hex}"
    delayed_key = f"test:reflexor:e2e:delayed:{uuid4().hex}"
    group = f"test:reflexor:e2e:group:{uuid4().hex}"
    consumer = f"test:reflexor:e2e:consumer:{uuid4().hex}"

    settings = ReflexorSettings(
        profile="dev",
        dry_run=True,
        database_url=database_url,
        workspace_root=workspace_root,
        enabled_scopes=["fs.read"],
        queue_backend="redis_streams",
        redis_url=redis_url,
        redis_stream_key=stream_key,
        redis_delayed_zset_key=delayed_key,
        redis_consumer_group=group,
        redis_consumer_name=consumer,
        redis_visibility_timeout_ms=1_000,
        queue_visibility_timeout_s=1.0,
    )

    container = AppContainer.build(settings=settings, tool_registry=registry, reflex_router=router)
    assert isinstance(container.queue, RedisStreamsQueue)

    executor = _build_executor(container)
    app = create_app(container=container)

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=True)

    entered_lifespan = False
    try:
        async with app.router.lifespan_context(app):
            entered_lifespan = True
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                event_body = {
                    "type": "webhook",
                    "source": "tests.e2e",
                    "payload": {"hello": "world"},
                    "dedupe_key": f"e2e:{uuid4().hex}",
                    "received_at_ms": 1_000,
                }

                response = await client.post("/events", json=event_body)
                assert response.status_code == 202
                assert response.headers.get("X-Request-ID")
                payload = response.json()
                event_id = str(payload["event_id"])
                run_id = str(payload["run_id"])
                assert payload["duplicate"] is False
                assert event_id
                assert run_id

                metrics = await client.get("/metrics")
                assert metrics.status_code == 200
                assert "events_received_total" in metrics.text

            uow = container.uow_factory()
            async with uow:
                packet_repo = container.repos.run_packet_repo(uow.session)
                packet = await packet_repo.get(run_id)
                assert packet is not None
                assert packet.event.event_id == event_id
                assert len(packet.tasks) == 1
                task = packet.tasks[0]
                assert task.status == TaskStatus.QUEUED
                assert task.tool_call is not None
                task_id = task.task_id
                tool_call_id = task.tool_call.tool_call_id

            runner = WorkerRunner(
                queue=container.queue,
                executor=executor,
                visibility_timeout_s=1.0,
                dequeue_wait_s=0.1,
                install_signal_handlers=False,
                close_queue_on_exit=False,
            )
            worker_task = asyncio.create_task(runner.run())

            try:
                deadline = time.monotonic() + 5.0
                last_task_status: str | None = None
                last_tool_call_status: str | None = None
                last_tool_results_count: int | None = None

                while time.monotonic() < deadline:
                    uow = container.uow_factory()
                    async with uow:
                        task_repo = container.repos.task_repo(uow.session)
                        tool_call_repo = container.repos.tool_call_repo(uow.session)
                        packet_repo = container.repos.run_packet_repo(uow.session)

                        db_task = await task_repo.get(task_id)
                        db_tool_call = await tool_call_repo.get(tool_call_id)
                        db_packet = await packet_repo.get(run_id)

                    assert db_task is not None
                    assert db_tool_call is not None
                    assert db_packet is not None

                    last_task_status = db_task.status.value
                    last_tool_call_status = db_tool_call.status.value
                    last_tool_results_count = len(db_packet.tool_results)

                    if (
                        db_task.status == TaskStatus.SUCCEEDED
                        and db_tool_call.status == ToolCallStatus.SUCCEEDED
                        and len(db_packet.tool_results) == 1
                        and len(tool.invocations) == 1
                    ):
                        break

                    await asyncio.sleep(0.05)

                assert tool.invocations, (
                    "expected MockTool to run; saw 0 invocations "
                    f"(task_status={last_task_status!r}, "
                    f"tool_call_status={last_tool_call_status!r}, "
                    f"tool_results={last_tool_results_count!r})"
                )

                invocation = tool.invocations[0]
                assert invocation.dry_run is True
                assert invocation.correlation_ids.get("event_id") == event_id
                assert invocation.correlation_ids.get("run_id") == run_id
                assert invocation.correlation_ids.get("task_id") == task_id
                assert invocation.correlation_ids.get("tool_call_id") == tool_call_id

                uow = container.uow_factory()
                async with uow:
                    task_repo = container.repos.task_repo(uow.session)
                    tool_call_repo = container.repos.tool_call_repo(uow.session)
                    packet_repo = container.repos.run_packet_repo(uow.session)

                    final_task = await task_repo.get(task_id)
                    final_tool_call = await tool_call_repo.get(tool_call_id)
                    final_packet = await packet_repo.get(run_id)

                assert final_task is not None
                assert final_tool_call is not None
                assert final_packet is not None

                assert final_task.status == TaskStatus.SUCCEEDED, (
                    f"expected task succeeded, got {final_task.status.value!r} "
                    f"(tool_call_status={final_tool_call.status.value!r}, "
                    f"tool_results={len(final_packet.tool_results)})"
                )
                assert final_tool_call.status == ToolCallStatus.SUCCEEDED

                assert len(final_packet.tool_results) == 1, (
                    "expected run packet to contain one tool_results entry after execution; "
                    f"got {len(final_packet.tool_results)} "
                    f"(task_status={final_task.status.value!r}, "
                    f"tool_call_status={final_tool_call.status.value!r})"
                )
                tool_result = final_packet.tool_results[0]
                assert tool_result.get("task_id") == task_id
                assert tool_result.get("tool_call_id") == tool_call_id
                assert tool_result.get("tool_name") == tool.manifest.name
                assert tool_result.get("status") == "succeeded"
            finally:
                runner.request_stop()
                await asyncio.wait_for(worker_task, timeout=5.0)
    finally:
        if not entered_lifespan:
            await container.aclose()
        await _cleanup_redis(redis_url, keys=[stream_key, delayed_key])


def test_e2e_api_ingest_to_redis_worker_persists_to_postgres(tmp_path: Path) -> None:
    database_url = _postgres_dsn()
    redis_url = _redis_url()

    _alembic_upgrade_head(database_url=database_url)
    asyncio.run(
        _run_e2e_flow(database_url=database_url, redis_url=redis_url, workspace_root=tmp_path)
    )

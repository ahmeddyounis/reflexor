from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import cast

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession

_REPO_ROOT = Path(__file__).resolve().parents[2]
_EXAMPLE_DIR = Path(__file__).resolve().parent
_DB_PATH = _EXAMPLE_DIR / "reflexor.db"
_SRC_ROOT = _REPO_ROOT / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from reflexor.api.container import AppContainer  # noqa: E402
from reflexor.config import ReflexorSettings  # noqa: E402
from reflexor.domain.models_event import Event  # noqa: E402
from reflexor.executor.concurrency import ConcurrencyLimiter  # noqa: E402
from reflexor.executor.idempotency import IdempotencyLedger  # noqa: E402
from reflexor.executor.retries import RetryPolicy  # noqa: E402
from reflexor.executor.service import (  # noqa: E402
    ExecutionDisposition,
    ExecutorRepoFactory,
    ExecutorService,
)
from reflexor.infra.db.models import Base  # noqa: E402
from reflexor.infra.db.repos import SqlAlchemyIdempotencyLedger  # noqa: E402
from reflexor.orchestrator.reflex_rules import RuleBasedReflexRouter  # noqa: E402
from reflexor.storage.uow import DatabaseSession  # noqa: E402
from reflexor.tools.fs_tool import FsWriteTextTool  # noqa: E402
from reflexor.tools.registry import ToolRegistry  # noqa: E402


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
        metrics=None,
    )


async def main() -> None:
    example_dir = _EXAMPLE_DIR
    db_path = _DB_PATH
    _create_schema(db_path)

    settings = ReflexorSettings(
        profile="dev",
        dry_run=True,
        workspace_root=example_dir,
        enabled_scopes=["fs.write"],
        approval_required_scopes=["fs.write"],
        http_allowed_domains=[],
        webhook_allowed_targets=[],
        database_url=f"sqlite+aiosqlite:///{db_path}",
    )

    registry = ToolRegistry()
    registry.register(FsWriteTextTool(settings=settings))

    router = RuleBasedReflexRouter.from_raw_rules(
        [
            {
                "rule_id": "approval_required_write",
                "match": {"event_type": "approval_demo"},
                "action": {
                    "kind": "fast_tool",
                    "tool_name": "fs.write_text",
                    "args_template": {
                        "path": "${payload.path}",
                        "text": "${payload.text}",
                        "create_parents": True,
                    },
                },
            }
        ]
    )

    container = AppContainer.build(settings=settings, tool_registry=registry, reflex_router=router)
    try:
        executor = _build_executor(container)

        now_ms = int(time.time() * 1000)
        event = Event(
            type="approval_demo",
            source="examples.approval_flow",
            received_at_ms=now_ms,
            payload={"path": "workspace/hello.txt", "text": "hello (dry-run)"},
        )

        outcome = await container.submit_events.submit_event(event)
        run_id = outcome.run_id

        lease_1 = await container.queue.dequeue(wait_s=0.0)
        if lease_1 is None:
            raise RuntimeError("expected an enqueued task envelope")

        report_1 = await executor.process_lease(lease_1)
        if report_1.disposition != ExecutionDisposition.WAITING_APPROVAL:
            raise RuntimeError(f"expected WAITING_APPROVAL, got {report_1.disposition}")
        if report_1.approval_id is None:
            raise RuntimeError("expected approval_id")

        approved = await container.approval_commands.approve(
            report_1.approval_id,
            decided_by="examples",
        )

        lease_2 = await container.queue.dequeue(wait_s=0.0)
        if lease_2 is None:
            raise RuntimeError("expected requeued task envelope after approval")

        report_2 = await executor.process_lease(lease_2)

        print("== Approval Flow Example ==")
        print(
            json.dumps(
                {
                    "run_id": run_id,
                    "approval_id": report_1.approval_id,
                    "approval_status": approved.status,
                    "first_disposition": report_1.disposition,
                    "second_disposition": report_2.disposition,
                    "second_result": report_2.result.model_dump(mode="json"),
                },
                indent=2,
                sort_keys=True,
                default=str,
            )
        )
        print()
        print("Tip: inspect via CLI (from repo root, with the example env sourced):")
        print("  .venv/bin/reflexor approvals list --json")
        print("  .venv/bin/reflexor tasks list --json")
        print("  .venv/bin/reflexor runs show <run_id> --json")
    finally:
        await container.aclose()


if __name__ == "__main__":
    asyncio.run(main())

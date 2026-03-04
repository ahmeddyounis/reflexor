from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from reflexor.config import ReflexorSettings, get_settings
from reflexor.domain.models_run_packet import RunPacket
from reflexor.executor.concurrency import ConcurrencyLimiter
from reflexor.executor.retries import RetryPolicy
from reflexor.executor.service import ExecutorRepoFactory, ExecutorService
from reflexor.infra.db.engine import create_async_engine, create_async_session_factory
from reflexor.infra.db.repos import (
    SqlAlchemyApprovalRepo,
    SqlAlchemyIdempotencyLedger,
    SqlAlchemyRunPacketRepo,
    SqlAlchemyRunRepo,
    SqlAlchemyTaskRepo,
    SqlAlchemyToolCallRepo,
)
from reflexor.infra.db.unit_of_work import SqlAlchemyUnitOfWork
from reflexor.infra.queue.factory import build_queue
from reflexor.observability.context import correlation_context
from reflexor.orchestrator.clock import Clock, SystemClock
from reflexor.orchestrator.queue import TaskEnvelope
from reflexor.replay.runner.io import _extract_packet, _read_json_file
from reflexor.replay.runner.mock_tools import ReplayInvocation, _AlwaysOkTool, _RecordedResultTool
from reflexor.replay.runner.packet import _build_replay_tasks, _extract_recorded_tool_results
from reflexor.replay.runner.settings import _derive_replay_settings
from reflexor.replay.runner.types import ReplayMode, ReplayOutcome
from reflexor.security.policy.approvals import InMemoryApprovalStore
from reflexor.security.policy.defaults import build_default_policy_rules
from reflexor.security.policy.enforcement import PolicyEnforcedToolRunner
from reflexor.security.policy.gate import PolicyGate
from reflexor.security.scopes import Scope
from reflexor.storage.ports import RunRecord
from reflexor.storage.uow import UnitOfWork
from reflexor.tools.registry import ToolRegistry
from reflexor.tools.runner import ToolRunner
from reflexor.tools.sdk import ToolResult


@dataclass(slots=True)
class ReplayRunner:
    settings: ReflexorSettings | None = None
    clock: Clock | None = None

    async def replay_from_file(self, path: str | Path, mode: ReplayMode) -> ReplayOutcome:
        base_settings = get_settings() if self.settings is None else self.settings
        replay_clock = SystemClock() if self.clock is None else self.clock

        file_path = Path(path)
        payload = await asyncio.to_thread(
            _read_json_file,
            file_path,
            max_bytes=int(base_settings.max_run_packet_bytes),
        )
        packet = _extract_packet(payload)

        replay_settings = _derive_replay_settings(base_settings, packet=packet, mode=mode)

        parent_run_id = packet.run_id
        replay_run_id = str(uuid4())

        tasks, tool_call_id_map = _build_replay_tasks(packet, replay_run_id=replay_run_id)
        tool_calls = [task.tool_call for task in tasks if task.tool_call is not None]

        recorded_results = _extract_recorded_tool_results(packet)
        results_for_replay: dict[str, ToolResult] = {}
        for original_id, replay_id in tool_call_id_map.items():
            result = recorded_results.get(original_id)
            if result is not None:
                results_for_replay[replay_id] = result

        # Build mock tools and registry.
        registry = ToolRegistry()
        tool_objs: list[object] = []
        tool_names: set[str] = {tc.tool_name for tc in tool_calls}
        tool_scopes: dict[str, str] = {}
        for tc in tool_calls:
            tool_scopes.setdefault(tc.tool_name, tc.permission_scope)

        if mode == ReplayMode.MOCK_TOOLS_RECORDED:
            for name in sorted(tool_names):
                scope = tool_scopes.get(name, Scope.FS_READ.value)
                recorded_tool = _RecordedResultTool(
                    tool_name=name,
                    permission_scope=scope,
                    results_by_tool_call_id=results_for_replay,
                    now_ms=replay_clock.now_ms,
                )
                registry.register(recorded_tool)
                tool_objs.append(recorded_tool)

        elif mode == ReplayMode.MOCK_TOOLS_SUCCESS:
            for name in sorted(tool_names):
                scope = tool_scopes.get(name, Scope.FS_READ.value)
                success_tool = _AlwaysOkTool(
                    tool_name=name,
                    permission_scope=scope,
                    now_ms=replay_clock.now_ms,
                )
                registry.register(success_tool)
                tool_objs.append(success_tool)

        tool_runner = ToolRunner(registry=registry, settings=replay_settings)
        policy_gate = PolicyGate(
            rules=build_default_policy_rules(),
            settings=replay_settings,
        )

        approvals = InMemoryApprovalStore()
        policy_runner = PolicyEnforcedToolRunner(
            registry=registry,
            runner=tool_runner,
            gate=policy_gate,
            approvals=approvals,
        )

        engine = create_async_engine(replay_settings)
        session_factory = create_async_session_factory(engine)

        def uow_factory() -> UnitOfWork:
            return SqlAlchemyUnitOfWork(session_factory)

        repos = ExecutorRepoFactory(
            task_repo=lambda session: SqlAlchemyTaskRepo(cast(AsyncSession, session)),
            tool_call_repo=lambda session: SqlAlchemyToolCallRepo(cast(AsyncSession, session)),
            approval_repo=lambda session: SqlAlchemyApprovalRepo(cast(AsyncSession, session)),
            run_packet_repo=lambda session: SqlAlchemyRunPacketRepo(
                cast(AsyncSession, session),
                settings=replay_settings,
            ),
        )

        queue = build_queue(replay_settings, now_ms=replay_clock.now_ms)
        retry_policy = RetryPolicy(
            max_attempts=3,
            base_delay_s=replay_settings.executor_retry_base_delay_s,
            max_delay_s=replay_settings.executor_retry_max_delay_s,
            jitter=replay_settings.executor_retry_jitter,
        )

        limiter = ConcurrencyLimiter(
            max_global=max(1, int(replay_settings.executor_max_concurrency)),
            per_tool=replay_settings.executor_per_tool_concurrency,
        )

        executor = ExecutorService(
            uow_factory=uow_factory,
            repos=repos,
            queue=queue,
            policy_runner=policy_runner,
            tool_registry=registry,
            idempotency_ledger=lambda session: SqlAlchemyIdempotencyLedger(
                cast(AsyncSession, session),
                settings=replay_settings,
            ),
            retry_policy=retry_policy,
            limiter=limiter,
            clock=replay_clock,
            metrics=None,
        )

        now_ms = int(replay_clock.now_ms())
        run_record = RunRecord(
            run_id=replay_run_id,
            parent_run_id=parent_run_id,
            created_at_ms=now_ms,
            started_at_ms=None,
            completed_at_ms=None,
        )

        replay_packet = RunPacket(
            run_id=replay_run_id,
            parent_run_id=parent_run_id,
            event=packet.event,
            reflex_decision=dict(packet.reflex_decision),
            plan=dict(packet.plan),
            tasks=tasks,
            tool_results=[],
            policy_decisions=[],
            created_at_ms=now_ms,
        )

        try:
            uow = uow_factory()
            async with uow:
                session = uow.session
                await SqlAlchemyRunRepo(cast(AsyncSession, session)).create(run_record)
                task_repo = SqlAlchemyTaskRepo(cast(AsyncSession, session))
                for task in tasks:
                    await task_repo.create(task)

                await SqlAlchemyRunPacketRepo(
                    cast(AsyncSession, session),
                    settings=replay_settings,
                ).create(replay_packet)

            if mode == ReplayMode.DRY_RUN_NO_TOOLS:
                return ReplayOutcome(
                    run_id=replay_run_id,
                    parent_run_id=parent_run_id,
                    mode=mode,
                    tasks_total=len(tasks),
                    tool_calls_total=len(tool_calls),
                    tool_invocations_total=0,
                    tool_invocations_by_name={},
                    dry_run=True,
                )

            with correlation_context(run_id=replay_run_id):
                for task in tasks:
                    envelope = TaskEnvelope(
                        envelope_id=str(uuid4()),
                        task_id=task.task_id,
                        run_id=replay_run_id,
                        attempt=0,
                        created_at_ms=now_ms,
                        available_at_ms=now_ms,
                    )
                    await queue.enqueue(envelope)

                max_steps = max(1, len(tasks) * 10)
                steps = 0
                while steps < max_steps:
                    steps += 1
                    lease = await queue.dequeue(wait_s=0.0)
                    if lease is None:
                        break
                    await executor.process_lease(lease)

        finally:
            await queue.aclose()
            await engine.dispose()

        invocations: list[ReplayInvocation] = []
        for tool_obj in tool_objs:
            invocations.extend(getattr(tool_obj, "invocations", []))

        counts_by_name: dict[str, int] = {}
        for invocation in invocations:
            counts_by_name[invocation.tool_name] = counts_by_name.get(invocation.tool_name, 0) + 1

        return ReplayOutcome(
            run_id=replay_run_id,
            parent_run_id=parent_run_id,
            mode=mode,
            tasks_total=len(tasks),
            tool_calls_total=len(tool_calls),
            tool_invocations_total=len(invocations),
            tool_invocations_by_name=counts_by_name,
            dry_run=True,
        )

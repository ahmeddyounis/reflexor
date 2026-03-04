"""Deterministic replay runner (safe by default).

This module provides a small helper to replay an exported run packet locally without
performing any real side effects (network/filesystem/webhook).

Replay modes:
- `dry_run_no_tools`: persist a replay run packet but never execute tools.
- `mock_tools_recorded`: execute tool calls using mock tools that return recorded ToolResults.
- `mock_tools_success`: execute tool calls using always-ok mock tools.

The runner enforces `dry_run=True` regardless of settings profile.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from reflexor.config import ReflexorSettings, get_settings
from reflexor.domain.enums import TaskStatus, ToolCallStatus
from reflexor.domain.models import Task, ToolCall
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
from reflexor.replay.exporter import EXPORT_SCHEMA_VERSION
from reflexor.security.net_safety import normalize_hostname, validate_and_normalize_url
from reflexor.security.policy.approvals import InMemoryApprovalStore
from reflexor.security.policy.enforcement import PolicyEnforcedToolRunner
from reflexor.security.policy.gate import PolicyGate
from reflexor.security.policy.rules import (
    ApprovalRequiredRule,
    NetworkAllowlistRule,
    ScopeEnabledRule,
    ScopeMatchesManifestRule,
    WorkspaceRule,
)
from reflexor.security.scopes import Scope
from reflexor.storage.ports import RunRecord
from reflexor.storage.uow import UnitOfWork
from reflexor.tools.registry import ToolRegistry
from reflexor.tools.runner import ToolRunner
from reflexor.tools.sdk import ToolContext, ToolManifest, ToolResult


class ReplayMode(StrEnum):
    DRY_RUN_NO_TOOLS = "dry_run_no_tools"
    MOCK_TOOLS_RECORDED = "mock_tools_recorded"
    MOCK_TOOLS_SUCCESS = "mock_tools_success"


class ReplayError(RuntimeError):
    """Raised when a replay cannot be performed."""


class RecordedArgs(BaseModel):
    """Permissive args model for replay tools (accepts arbitrary JSON)."""

    model_config = ConfigDict(extra="allow", frozen=True)


@dataclass(frozen=True, slots=True)
class ReplayInvocation:
    tool_name: str
    tool_call_id: str | None
    called_at_ms: int
    dry_run: bool
    result: ToolResult


@dataclass(slots=True)
class _RecordedResultTool:
    tool_name: str
    permission_scope: str
    results_by_tool_call_id: dict[str, ToolResult]
    now_ms: Callable[[], int]

    manifest: ToolManifest = field(init=False)
    invocations: list[ReplayInvocation] = field(default_factory=list)

    ArgsModel = RecordedArgs

    def __post_init__(self) -> None:
        self.manifest = ToolManifest(
            name=self.tool_name,
            version="0.1.0",
            description="Replay tool returning recorded ToolResults.",
            permission_scope=self.permission_scope,
            side_effects=False,
            idempotent=False,
            default_timeout_s=5,
            max_output_bytes=64_000,
            tags=["replay", "mock"],
        )

    async def run(self, args: RecordedArgs, ctx: ToolContext) -> ToolResult:
        _ = args
        tool_call_id = ctx.correlation_ids.get("tool_call_id")
        tool_call_id_str = tool_call_id if isinstance(tool_call_id, str) else None
        result = (
            self.results_by_tool_call_id.get(tool_call_id_str)
            if tool_call_id_str is not None
            else None
        )
        if result is None:
            result = ToolResult(
                ok=False,
                error_code="REPLAY_MISSING_RESULT",
                error_message="no recorded ToolResult found for tool_call_id",
                debug={"tool_call_id": tool_call_id},
            )

        self.invocations.append(
            ReplayInvocation(
                tool_name=self.tool_name,
                tool_call_id=tool_call_id_str,
                called_at_ms=int(self.now_ms()),
                dry_run=bool(ctx.dry_run),
                result=result,
            )
        )
        return result


@dataclass(slots=True)
class _AlwaysOkTool:
    tool_name: str
    permission_scope: str
    now_ms: Callable[[], int]

    manifest: ToolManifest = field(init=False)
    invocations: list[ReplayInvocation] = field(default_factory=list)

    ArgsModel = RecordedArgs

    def __post_init__(self) -> None:
        self.manifest = ToolManifest(
            name=self.tool_name,
            version="0.1.0",
            description="Replay tool returning ok=true.",
            permission_scope=self.permission_scope,
            side_effects=False,
            idempotent=False,
            default_timeout_s=5,
            max_output_bytes=64_000,
            tags=["replay", "mock"],
        )

    async def run(self, args: RecordedArgs, ctx: ToolContext) -> ToolResult:
        _ = args
        tool_call_id = ctx.correlation_ids.get("tool_call_id")
        tool_call_id_str = tool_call_id if isinstance(tool_call_id, str) else None
        result = ToolResult(
            ok=True,
            data={"tool_call_id": tool_call_id, "tool_name": self.tool_name},
        )
        self.invocations.append(
            ReplayInvocation(
                tool_name=self.tool_name,
                tool_call_id=tool_call_id_str,
                called_at_ms=int(self.now_ms()),
                dry_run=bool(ctx.dry_run),
                result=result,
            )
        )
        return result


class ReplayOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    parent_run_id: str | None
    mode: ReplayMode
    tasks_total: int
    tool_calls_total: int
    tool_invocations_total: int
    tool_invocations_by_name: dict[str, int] = Field(default_factory=dict)
    dry_run: bool = True


def _read_json_file(path: Path, *, max_bytes: int) -> object:
    if max_bytes <= 0:
        raise ValueError("max_bytes must be > 0")

    if not path.exists():
        raise FileNotFoundError(str(path))
    if not path.is_file():
        raise ReplayError(f"not a file: {path}")

    size = path.stat().st_size
    if size > max_bytes:
        raise ReplayError(f"replay file is too large ({size} bytes); max is {max_bytes}")

    data = path.read_bytes()
    if len(data) > max_bytes:
        raise ReplayError(f"replay file is too large ({len(data)} bytes); max is {max_bytes}")

    try:
        return json.loads(data)
    except json.JSONDecodeError as exc:
        raise ReplayError(f"invalid JSON: {exc.msg}") from exc


def _extract_packet(payload: object) -> RunPacket:
    if not isinstance(payload, dict):
        raise ReplayError("export JSON must be an object")

    schema_version = payload.get("schema_version")
    if schema_version != EXPORT_SCHEMA_VERSION:
        raise ReplayError(
            f"unsupported schema_version: {schema_version!r}; expected {EXPORT_SCHEMA_VERSION}"
        )

    packet_obj = payload.get("packet")
    if not isinstance(packet_obj, dict):
        raise ReplayError("export JSON must contain a 'packet' object")

    try:
        return RunPacket.model_validate(packet_obj)
    except ValidationError as exc:
        raise ReplayError("exported packet is not a valid RunPacket") from exc


def _safe_default_enabled_scopes(scopes: set[str]) -> list[str]:
    safe = [Scope.FS_READ.value] if Scope.FS_READ.value in scopes else [Scope.FS_READ.value]
    return safe


def _derive_replay_settings(
    base: ReflexorSettings, *, packet: RunPacket, mode: ReplayMode
) -> ReflexorSettings:
    base_payload = base.model_dump()
    base_payload["dry_run"] = True

    scopes_used = {
        task.tool_call.permission_scope
        for task in packet.tasks
        if task.tool_call is not None and task.tool_call.permission_scope
    }
    known_scopes = {scope.value for scope in Scope}
    scopes_used = {scope for scope in scopes_used if scope in known_scopes}

    if mode == ReplayMode.DRY_RUN_NO_TOOLS:
        enabled_scopes = _safe_default_enabled_scopes(scopes_used)
    else:
        enabled_scopes = (
            sorted(scopes_used) if scopes_used else _safe_default_enabled_scopes(scopes_used)
        )

    base_payload["enabled_scopes"] = enabled_scopes
    base_payload["approval_required_scopes"] = []

    if mode != ReplayMode.DRY_RUN_NO_TOOLS:
        http_domains, webhook_targets = _derive_allowlists(packet)
        base_payload["http_allowed_domains"] = http_domains
        base_payload["webhook_allowed_targets"] = webhook_targets
    else:
        base_payload["http_allowed_domains"] = []
        base_payload["webhook_allowed_targets"] = []

    try:
        return ReflexorSettings.model_validate(base_payload)
    except ValidationError as exc:
        raise ReplayError("failed to build replay settings") from exc


def _derive_allowlists(packet: RunPacket) -> tuple[list[str], list[str]]:
    http_domains: list[str] = []
    webhook_targets: list[str] = []

    for task in packet.tasks:
        tool_call = task.tool_call
        if tool_call is None:
            continue

        args = tool_call.args
        url_value = None
        for key in ("url", "target_url", "webhook_url", "endpoint_url"):
            raw = args.get(key)
            if isinstance(raw, str) and raw.strip():
                url_value = raw.strip()
                break

        if url_value is None:
            continue

        host = urlsplit(url_value).hostname
        if host:
            try:
                http_domains.append(normalize_hostname(host))
            except ValueError:
                pass

        try:
            normalized = validate_and_normalize_url(
                url_value,
                require_https=True,
                allowed_domains=None,
            )
        except ValueError:
            normalized = None

        if normalized is not None and tool_call.permission_scope == Scope.WEBHOOK_EMIT.value:
            webhook_targets.append(normalized)

    return http_domains, webhook_targets


def _build_replay_tasks(
    packet: RunPacket, *, replay_run_id: str
) -> tuple[list[Task], dict[str, str]]:
    task_id_map: dict[str, str] = {task.task_id: str(uuid4()) for task in packet.tasks}
    tool_call_id_map: dict[str, str] = {}

    tasks: list[Task] = []
    for task in packet.tasks:
        tool_call = task.tool_call
        replay_tool_call: ToolCall | None = None
        if tool_call is not None:
            new_tool_call_id = str(uuid4())
            tool_call_id_map[tool_call.tool_call_id] = new_tool_call_id
            replay_tool_call = ToolCall(
                tool_call_id=new_tool_call_id,
                tool_name=tool_call.tool_name,
                args=dict(tool_call.args),
                permission_scope=tool_call.permission_scope,
                idempotency_key=tool_call.idempotency_key,
                status=ToolCallStatus.PENDING,
                created_at_ms=tool_call.created_at_ms,
                started_at_ms=None,
                completed_at_ms=None,
                result_ref=None,
            )

        new_task_id = task_id_map[task.task_id]
        depends_on = [task_id_map.get(dep, dep) for dep in task.depends_on]

        metadata = dict(task.metadata)
        metadata.setdefault("replay", {})
        if isinstance(metadata["replay"], dict):
            metadata["replay"].update(
                {
                    "original_task_id": task.task_id,
                    "original_run_id": packet.run_id,
                    "original_tool_call_id": None if tool_call is None else tool_call.tool_call_id,
                }
            )

        tasks.append(
            Task(
                task_id=new_task_id,
                run_id=replay_run_id,
                name=task.name,
                status=TaskStatus.PENDING,
                tool_call=replay_tool_call,
                attempts=0,
                max_attempts=task.max_attempts,
                timeout_s=task.timeout_s,
                depends_on=depends_on,
                created_at_ms=task.created_at_ms,
                started_at_ms=None,
                completed_at_ms=None,
                labels=list(task.labels),
                metadata=metadata,
            )
        )

    return tasks, tool_call_id_map


def _extract_recorded_tool_results(packet: RunPacket) -> dict[str, ToolResult]:
    results: dict[str, ToolResult] = {}
    for entry in packet.tool_results:
        tool_call_id = entry.get("tool_call_id")
        if not isinstance(tool_call_id, str) or not tool_call_id.strip():
            continue

        candidate = entry.get("result_summary")
        if not isinstance(candidate, Mapping):
            candidate = entry.get("result")
        if not isinstance(candidate, Mapping):
            continue

        try:
            result = ToolResult.model_validate(candidate)
        except ValidationError:
            continue

        results[tool_call_id] = result
    return results


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
            rules=[
                ScopeMatchesManifestRule(),
                ScopeEnabledRule(),
                NetworkAllowlistRule(),
                WorkspaceRule(),
                ApprovalRequiredRule(),
            ],
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


__all__ = ["ReplayError", "ReplayMode", "ReplayOutcome", "ReplayRunner"]

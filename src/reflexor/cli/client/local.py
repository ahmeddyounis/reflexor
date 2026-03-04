from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from reflexor.application.approvals_service import ApprovalCommandService
from reflexor.application.services import (
    EventSubmissionService,
    RunQueryService,
    TaskQueryService,
)
from reflexor.application.suppressions_service import (
    EventSuppressionCommandService,
    EventSuppressionQueryService,
)
from reflexor.cli.client.protocol import ReplayModeStr
from reflexor.cli.client.serialization import (
    _page,
    _run_summary_to_dict,
    _submit_outcome_to_dict,
    _suppression_to_dict,
    _task_summary_to_dict,
)
from reflexor.config import ReflexorSettings
from reflexor.domain.enums import ApprovalStatus, RunStatus, TaskStatus
from reflexor.domain.models_event import Event
from reflexor.tools.registry import ToolRegistry


@dataclass(frozen=True, slots=True)
class LocalClient:
    """In-process CLI client powered by application services."""

    settings: ReflexorSettings
    submitter: EventSubmissionService
    run_queries: RunQueryService
    task_queries: TaskQueryService
    approval_commands: ApprovalCommandService
    suppression_queries: EventSuppressionQueryService
    suppression_commands: EventSuppressionCommandService
    tool_registry: ToolRegistry
    aclose_callback: Callable[[], Awaitable[None]] | None = field(
        default=None, repr=False, compare=False
    )

    async def submit_event(self, event: Event) -> dict[str, object]:
        outcome = await self.submitter.submit_event(event)
        return _submit_outcome_to_dict(outcome)

    async def list_runs(
        self,
        *,
        limit: int,
        offset: int,
        status: RunStatus | None = None,
        since_ms: int | None = None,
    ) -> dict[str, object]:
        summaries, total = await self.run_queries.list_runs(
            limit=limit,
            offset=offset,
            status=status,
            since_ms=since_ms,
        )
        items = [_run_summary_to_dict(item) for item in summaries]
        return _page(limit=limit, offset=offset, total=total, items=items)

    async def get_run(self, run_id: str) -> dict[str, object]:
        summary = await self.run_queries.get_run_summary(run_id)
        if summary is None:
            raise KeyError(f"run not found: {run_id!r}")

        packet = await self.run_queries.get_run_packet(run_id)
        packet_dict: dict[str, object] = {}
        if packet is not None:
            packet_dict = cast(dict[str, object], packet.model_dump(mode="json"))

        return {"summary": _run_summary_to_dict(summary), "run_packet": packet_dict}

    async def list_tasks(
        self,
        *,
        limit: int,
        offset: int,
        run_id: str | None = None,
        status: TaskStatus | None = None,
    ) -> dict[str, object]:
        summaries, total = await self.task_queries.list_tasks(
            limit=limit,
            offset=offset,
            run_id=run_id,
            status=status,
        )
        items = [_task_summary_to_dict(item) for item in summaries]
        return _page(limit=limit, offset=offset, total=total, items=items)

    async def list_approvals(
        self,
        *,
        limit: int,
        offset: int,
        status: ApprovalStatus | None = None,
        run_id: str | None = None,
    ) -> dict[str, object]:
        approvals, total = await self.approval_commands.list_approvals(
            limit=limit,
            offset=offset,
            status=status,
            run_id=run_id,
        )
        items = [
            cast(dict[str, object], approval.model_dump(mode="json")) for approval in approvals
        ]
        return _page(limit=limit, offset=offset, total=total, items=items)

    async def approve(
        self, approval_id: str, *, decided_by: str | None = None
    ) -> dict[str, object]:
        approval = await self.approval_commands.approve(approval_id, decided_by=decided_by)
        return {"approval": cast(dict[str, object], approval.model_dump(mode="json"))}

    async def deny(self, approval_id: str, *, decided_by: str | None = None) -> dict[str, object]:
        approval = await self.approval_commands.deny(approval_id, decided_by=decided_by)
        return {"approval": cast(dict[str, object], approval.model_dump(mode="json"))}

    async def list_tools(self) -> list[dict[str, object]]:
        manifests = self.tool_registry.list_manifests()
        return [cast(dict[str, object], m.model_dump(mode="json")) for m in manifests]

    async def health(self) -> dict[str, object]:
        return {"ok": True}

    async def aclose(self) -> None:
        if self.aclose_callback is None:
            return
        await self.aclose_callback()

    async def export_run_packet(
        self,
        run_id: str,
        out_path: str | Path,
        *,
        include_tasks: bool = True,
    ) -> dict[str, object]:
        from reflexor.replay.exporter import export_run_packet as export_run_packet_file

        normalized_run_id = run_id.strip()
        exported_path = await export_run_packet_file(
            normalized_run_id,
            out_path,
            include_tasks=include_tasks,
            settings=self.settings,
        )
        return {
            "ok": True,
            "run_id": normalized_run_id,
            "out_path": str(exported_path),
        }

    async def import_run_packet(
        self,
        path: str | Path,
        *,
        parent_run_id: str | None = None,
    ) -> dict[str, object]:
        from reflexor.replay.importer import import_run_packet as import_run_packet_file

        new_run_id = await import_run_packet_file(
            path,
            parent_run_id=parent_run_id,
            settings=self.settings,
        )
        return {
            "ok": True,
            "run_id": new_run_id,
        }

    async def replay_run_packet(
        self,
        path: str | Path,
        *,
        mode: ReplayModeStr,
    ) -> dict[str, object]:
        from reflexor.replay.runner import ReplayMode, ReplayRunner

        runner = ReplayRunner(settings=self.settings)
        outcome = await runner.replay_from_file(path, ReplayMode(mode))
        payload = cast(dict[str, object], outcome.model_dump(mode="json"))
        return {"ok": True, **payload}

    async def list_suppressions(self, *, limit: int, offset: int) -> dict[str, object]:
        records, total = await self.suppression_queries.list_active(limit=limit, offset=offset)
        items = [_suppression_to_dict(item) for item in records]
        return _page(limit=limit, offset=offset, total=total, items=items)

    async def clear_suppression(
        self, signature_hash: str, *, cleared_by: str | None = None
    ) -> dict[str, object]:
        record = await self.suppression_commands.clear(
            signature_hash,
            cleared_by=cleared_by,
            cleared_request_id=None,
        )
        return {
            "ok": True,
            "signature_hash": record.signature_hash,
            "cleared_at_ms": record.cleared_at_ms,
            "cleared_by": record.cleared_by,
        }


__all__ = ["LocalClient"]

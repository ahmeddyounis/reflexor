"""CLI client abstractions (DIP).

The CLI can operate in two modes:
- Local mode: direct in-process calls via application services, repos/UoW, and the queue.
- API mode: remote calls to the FastAPI service via HTTP.

Commands should depend on the `CliClient` protocol and avoid direct ORM/database access.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol, TypeAlias, cast

import httpx

from reflexor.application.approvals_service import ApprovalCommandService
from reflexor.application.services import (
    EventSubmissionService,
    RunQueryService,
    SubmitEventOutcome,
    TaskQueryService,
)
from reflexor.application.suppressions_service import (
    EventSuppressionCommandService,
    EventSuppressionQueryService,
)
from reflexor.config import ReflexorSettings
from reflexor.domain.enums import ApprovalStatus, RunStatus, TaskStatus
from reflexor.domain.models_event import Event
from reflexor.storage.ports import EventSuppressionRecord as StoredEventSuppressionRecord
from reflexor.storage.ports import RunSummary as StoredRunSummary
from reflexor.storage.ports import TaskSummary as StoredTaskSummary
from reflexor.tools.registry import ToolRegistry

ReplayModeStr: TypeAlias = Literal[
    "dry_run_no_tools",
    "mock_tools_recorded",
    "mock_tools_success",
]


class CliClient(Protocol):
    async def submit_event(self, event: Event) -> dict[str, object]: ...

    async def list_runs(
        self,
        *,
        limit: int,
        offset: int,
        status: RunStatus | None = None,
        since_ms: int | None = None,
    ) -> dict[str, object]: ...

    async def get_run(self, run_id: str) -> dict[str, object]: ...

    async def list_tasks(
        self,
        *,
        limit: int,
        offset: int,
        run_id: str | None = None,
        status: TaskStatus | None = None,
    ) -> dict[str, object]: ...

    async def list_approvals(
        self,
        *,
        limit: int,
        offset: int,
        status: ApprovalStatus | None = None,
        run_id: str | None = None,
    ) -> dict[str, object]: ...

    async def approve(
        self, approval_id: str, *, decided_by: str | None = None
    ) -> dict[str, object]: ...

    async def deny(
        self, approval_id: str, *, decided_by: str | None = None
    ) -> dict[str, object]: ...

    async def list_tools(self) -> list[dict[str, object]]: ...

    async def health(self) -> dict[str, object]: ...

    async def export_run_packet(
        self,
        run_id: str,
        out_path: str | Path,
        *,
        include_tasks: bool = True,
    ) -> dict[str, object]: ...

    async def import_run_packet(
        self,
        path: str | Path,
        *,
        parent_run_id: str | None = None,
    ) -> dict[str, object]: ...

    async def replay_run_packet(
        self,
        path: str | Path,
        *,
        mode: ReplayModeStr,
    ) -> dict[str, object]: ...

    async def list_suppressions(self, *, limit: int, offset: int) -> dict[str, object]: ...

    async def clear_suppression(
        self, signature_hash: str, *, cleared_by: str | None = None
    ) -> dict[str, object]: ...

    async def aclose(self) -> None: ...


def _page(
    *,
    limit: int,
    offset: int,
    total: int,
    items: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "limit": int(limit),
        "offset": int(offset),
        "total": int(total),
        "items": items,
    }


def _run_summary_to_dict(summary: StoredRunSummary) -> dict[str, object]:
    return {
        "run_id": summary.run_id,
        "created_at_ms": int(summary.created_at_ms),
        "started_at_ms": summary.started_at_ms,
        "completed_at_ms": summary.completed_at_ms,
        "status": str(summary.status),
        "event_type": summary.event_type,
        "event_source": summary.event_source,
        "tasks_total": int(summary.tasks_total),
        "tasks_pending": int(summary.tasks_pending),
        "tasks_queued": int(summary.tasks_queued),
        "tasks_running": int(summary.tasks_running),
        "tasks_succeeded": int(summary.tasks_succeeded),
        "tasks_failed": int(summary.tasks_failed),
        "tasks_canceled": int(summary.tasks_canceled),
        "approvals_total": int(summary.approvals_total),
        "approvals_pending": int(summary.approvals_pending),
    }


def _task_summary_to_dict(summary: StoredTaskSummary) -> dict[str, object]:
    return {
        "task_id": summary.task_id,
        "run_id": summary.run_id,
        "name": summary.name,
        "status": str(summary.status),
        "attempts": int(summary.attempts),
        "max_attempts": int(summary.max_attempts),
        "timeout_s": int(summary.timeout_s),
        "depends_on": list(summary.depends_on),
        "tool_call_id": summary.tool_call_id,
        "tool_name": summary.tool_name,
        "permission_scope": summary.permission_scope,
        "idempotency_key": summary.idempotency_key,
        "tool_call_status": (
            None if summary.tool_call_status is None else str(summary.tool_call_status)
        ),
    }


def _submit_outcome_to_dict(outcome: SubmitEventOutcome) -> dict[str, object]:
    return {
        "ok": True,
        "event_id": outcome.event_id,
        "run_id": outcome.run_id,
        "duplicate": bool(outcome.duplicate),
    }


def _suppression_to_dict(record: StoredEventSuppressionRecord) -> dict[str, object]:
    return {
        "signature_hash": record.signature_hash,
        "event_type": record.event_type,
        "event_source": record.event_source,
        "signature": record.signature,
        "count": int(record.count),
        "threshold": int(record.threshold),
        "window_ms": int(record.window_ms),
        "window_start_ms": int(record.window_start_ms),
        "suppressed_until_ms": record.suppressed_until_ms,
        "expires_at_ms": int(record.expires_at_ms),
        "resume_required": bool(record.resume_required),
        "cleared_at_ms": record.cleared_at_ms,
        "cleared_by": record.cleared_by,
        "cleared_request_id": record.cleared_request_id,
        "created_at_ms": int(record.created_at_ms),
        "updated_at_ms": int(record.updated_at_ms),
    }


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


@dataclass(slots=True)
class ApiClient:
    """HTTP-backed CLI client for remote operation via the Reflexor API."""

    base_url: str
    admin_api_key: str | None = None
    http: httpx.AsyncClient | None = None
    _owns_http: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.http is None:
            self.http = httpx.AsyncClient(timeout=10.0)
            self._owns_http = True

    def _url(self, path: str) -> str:
        normalized_base = self.base_url.rstrip("/")
        normalized_path = path if path.startswith("/") else f"/{path}"
        return f"{normalized_base}{normalized_path}"

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/json"}
        if self.admin_api_key:
            headers["X-API-Key"] = self.admin_api_key
        return headers

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str | int | float | bool | None] | None = None,
        json_body: Mapping[str, object] | None = None,
    ) -> object:
        http = self.http
        assert http is not None
        response = await http.request(
            method=method,
            url=self._url(path),
            headers=self._headers(),
            params=params,
            json=json_body,
        )
        response.raise_for_status()
        return response.json()

    async def submit_event(self, event: Event) -> dict[str, object]:
        payload = {
            "type": event.type,
            "source": event.source,
            "payload": event.payload,
            "dedupe_key": event.dedupe_key,
            "received_at_ms": int(event.received_at_ms),
        }
        data = await self._request_json("POST", "/v1/events", json_body=payload)
        return cast(dict[str, object], data)

    async def list_runs(
        self,
        *,
        limit: int,
        offset: int,
        status: RunStatus | None = None,
        since_ms: int | None = None,
    ) -> dict[str, object]:
        params: dict[str, str | int | float | bool | None] = {
            "limit": int(limit),
            "offset": int(offset),
        }
        if status is not None:
            params["status"] = str(status)
        if since_ms is not None:
            params["since_ms"] = int(since_ms)
        data = await self._request_json("GET", "/v1/runs", params=params)
        return cast(dict[str, object], data)

    async def get_run(self, run_id: str) -> dict[str, object]:
        data = await self._request_json("GET", f"/v1/runs/{run_id}")
        return cast(dict[str, object], data)

    async def list_tasks(
        self,
        *,
        limit: int,
        offset: int,
        run_id: str | None = None,
        status: TaskStatus | None = None,
    ) -> dict[str, object]:
        params: dict[str, str | int | float | bool | None] = {
            "limit": int(limit),
            "offset": int(offset),
        }
        if run_id is not None:
            params["run_id"] = run_id
        if status is not None:
            params["status"] = str(status)
        data = await self._request_json("GET", "/v1/tasks", params=params)
        return cast(dict[str, object], data)

    async def list_approvals(
        self,
        *,
        limit: int,
        offset: int,
        status: ApprovalStatus | None = None,
        run_id: str | None = None,
    ) -> dict[str, object]:
        params: dict[str, str | int | float | bool | None] = {
            "limit": int(limit),
            "offset": int(offset),
        }
        if status is not None:
            params["status"] = str(status)
        if run_id is not None:
            params["run_id"] = run_id
        data = await self._request_json("GET", "/v1/approvals", params=params)
        return cast(dict[str, object], data)

    async def approve(
        self, approval_id: str, *, decided_by: str | None = None
    ) -> dict[str, object]:
        json_body = None if decided_by is None else {"decided_by": decided_by}
        data = await self._request_json(
            "POST", f"/v1/approvals/{approval_id}/approve", json_body=json_body
        )
        return cast(dict[str, object], data)

    async def deny(self, approval_id: str, *, decided_by: str | None = None) -> dict[str, object]:
        json_body = None if decided_by is None else {"decided_by": decided_by}
        data = await self._request_json(
            "POST", f"/v1/approvals/{approval_id}/deny", json_body=json_body
        )
        return cast(dict[str, object], data)

    async def list_tools(self) -> list[dict[str, object]]:
        raise NotImplementedError("list_tools is not exposed via the API yet")

    async def health(self) -> dict[str, object]:
        data = await self._request_json("GET", "/healthz")
        return cast(dict[str, object], data)

    async def aclose(self) -> None:
        if not self._owns_http:
            return
        http = self.http
        if http is None:
            return
        await http.aclose()
        self.http = None
        self._owns_http = False

    async def export_run_packet(
        self,
        run_id: str,
        out_path: str | Path,
        *,
        include_tasks: bool = True,
    ) -> dict[str, object]:
        _ = (run_id, out_path, include_tasks)
        raise NotImplementedError("run export is not exposed via the API yet")

    async def import_run_packet(
        self,
        path: str | Path,
        *,
        parent_run_id: str | None = None,
    ) -> dict[str, object]:
        _ = (path, parent_run_id)
        raise NotImplementedError("run import is not exposed via the API yet")

    async def replay_run_packet(
        self,
        path: str | Path,
        *,
        mode: ReplayModeStr,
    ) -> dict[str, object]:
        _ = (path, mode)
        raise NotImplementedError("run replay is not exposed via the API yet")

    async def list_suppressions(self, *, limit: int, offset: int) -> dict[str, object]:
        params: dict[str, str | int | float | bool | None] = {
            "limit": int(limit),
            "offset": int(offset),
        }
        data = await self._request_json("GET", "/v1/suppressions", params=params)
        return cast(dict[str, object], data)

    async def clear_suppression(
        self, signature_hash: str, *, cleared_by: str | None = None
    ) -> dict[str, object]:
        json_body = None if cleared_by is None else {"cleared_by": cleared_by}
        data = await self._request_json(
            "POST",
            f"/v1/suppressions/{signature_hash}/clear",
            json_body=json_body,
        )
        return cast(dict[str, object], data)


__all__ = ["ApiClient", "CliClient", "LocalClient", "ReplayModeStr"]

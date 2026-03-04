from __future__ import annotations

from pathlib import Path
from typing import Literal, Protocol, TypeAlias

from reflexor.domain.enums import ApprovalStatus, RunStatus, TaskStatus
from reflexor.domain.models_event import Event

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


__all__ = ["CliClient", "ReplayModeStr"]

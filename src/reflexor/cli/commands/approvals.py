from __future__ import annotations

import sys
from typing import Any, cast

import typer

from reflexor.cli import output
from reflexor.cli.client import CliClient
from reflexor.cli.container import CliContainer
from reflexor.domain.enums import ApprovalStatus

MAX_PAGE_LIMIT = 200

JSON_OPT = typer.Option(False, "--json", help="Output machine-readable JSON.")
PRETTY_OPT = typer.Option(False, "--pretty", help="Pretty-print JSON (implies --json).")
LIMIT_OPT = typer.Option(50, min=0, max=MAX_PAGE_LIMIT)
OFFSET_OPT = typer.Option(0, min=0)
STATUS_OPT = typer.Option(None, "--status", help="Filter by status.")
PENDING_ONLY_OPT = typer.Option(False, "--pending-only", help="Show only pending approvals.")
RUN_ID_OPT = typer.Option(None, "--run-id", help="Filter by run_id.")
SCOPE_OPT = typer.Option(None, "--scope", help="Filter by tool permission scope (best-effort).")
DECIDED_BY_OPT = typer.Option(None, "--decided-by")

_SCOPE_PREFIX = "permission_scope:"


def _approval_permission_scope(item: dict[str, object]) -> str | None:
    value = item.get("permission_scope")
    if isinstance(value, str):
        trimmed = value.strip()
        return trimmed or None

    preview = item.get("preview")
    if not isinstance(preview, str):
        return None

    for line in preview.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith(_SCOPE_PREFIX):
            parsed = stripped.split(":", 1)[-1].strip()
            return parsed or None

    return None


def _approval_matches_scope(item: dict[str, object], *, scope: str) -> bool:
    normalized_scope = scope.strip()
    if not normalized_scope:
        return False
    return _approval_permission_scope(item) == normalized_scope


async def _list_approvals_with_scope(
    client: CliClient,
    *,
    limit: int,
    offset: int,
    status: ApprovalStatus | None,
    run_id: str | None,
    scope: str | None,
) -> dict[str, object]:
    if scope is None:
        return await client.list_approvals(limit=limit, offset=offset, status=status, run_id=run_id)

    normalized_scope = scope.strip()
    if not normalized_scope:
        raise ValueError("scope must be non-empty when provided")

    # Apply scope filtering client-side (API does not expose scope as a query filter).
    chunk_limit = MAX_PAGE_LIMIT
    chunk_offset = 0
    matched: list[dict[str, object]] = []
    total_unfiltered: int | None = None

    while True:
        page = await client.list_approvals(
            limit=chunk_limit,
            offset=chunk_offset,
            status=status,
            run_id=run_id,
        )
        items_obj = page.get("items")
        items = (
            [item for item in items_obj if isinstance(item, dict)]
            if isinstance(items_obj, list)
            else []
        )
        if total_unfiltered is None:
            try:
                total_unfiltered = int(cast(Any, page.get("total", 0)))
            except Exception:
                total_unfiltered = 0

        for item in items:
            if _approval_matches_scope(item, scope=normalized_scope):
                matched.append(item)

        chunk_offset += chunk_limit
        if total_unfiltered is not None and chunk_offset >= total_unfiltered:
            break
        if len(items) < chunk_limit:
            break

    window = matched[offset : offset + limit]
    return {"limit": limit, "offset": offset, "total": len(matched), "items": window}


def _require_prod_approval_confirmation(
    container: CliContainer,
    *,
    json_enabled: bool,
    pretty_enabled: bool,
) -> None:
    if container.settings.profile != "prod":
        return
    if container.assume_yes:
        return

    message = "approving in prod requires --yes"
    if json_enabled:
        output.print_json(
            {
                "ok": False,
                "error_code": "confirmation_required",
                "message": message,
            },
            pretty=pretty_enabled,
        )
        raise typer.Exit(2) from None

    # Best-effort interactive confirmation. If stdin is not interactive, require `--yes`.
    if not sys.stdin.isatty():
        output.abort(message, exit_code=2)

    try:
        confirmed = typer.confirm(
            "Approve this action? It may trigger side effects if the worker runs with dry-run off.",
            default=False,
        )
    except (EOFError, OSError):
        confirmed = False

    if not confirmed:
        output.abort("aborted", exit_code=2)


def register(app: typer.Typer) -> None:
    approvals_app = typer.Typer(help="Approval workflows.")
    app.add_typer(approvals_app, name="approvals")

    @approvals_app.command("list")
    def list_approvals(
        ctx: typer.Context,
        limit: int = LIMIT_OPT,
        offset: int = OFFSET_OPT,
        status: ApprovalStatus | None = STATUS_OPT,
        pending_only: bool = PENDING_ONLY_OPT,
        run_id: str | None = RUN_ID_OPT,
        scope: str | None = SCOPE_OPT,
        json_output: bool = JSON_OPT,
        pretty: bool = PRETTY_OPT,
    ) -> None:
        container = ctx.obj
        if not isinstance(container, CliContainer):
            output.abort("internal error: invalid CLI context object")

        if pending_only and status is not None and status != ApprovalStatus.PENDING:
            output.abort("--pending-only conflicts with --status", exit_code=2)
        effective_status = ApprovalStatus.PENDING if pending_only else status

        page = container.run(
            lambda client: _list_approvals_with_scope(
                client,
                limit=limit,
                offset=offset,
                status=effective_status,
                run_id=run_id,
                scope=scope,
            )
        )

        pretty_enabled = bool(container.output_pretty or pretty)
        json_enabled = bool(container.output_json or json_output or pretty_enabled)
        if json_enabled:
            output.print_json(page, pretty=pretty_enabled)
            return
        output.print_approvals_table(page)

    @approvals_app.command("approve")
    def approve(
        ctx: typer.Context,
        approval_id: str,
        decided_by: str | None = DECIDED_BY_OPT,
        json_output: bool = JSON_OPT,
        pretty: bool = PRETTY_OPT,
    ) -> None:
        container = ctx.obj
        if not isinstance(container, CliContainer):
            output.abort("internal error: invalid CLI context object")

        pretty_enabled = bool(container.output_pretty or pretty)
        json_enabled = bool(container.output_json or json_output or pretty_enabled)
        _require_prod_approval_confirmation(
            container, json_enabled=json_enabled, pretty_enabled=pretty_enabled
        )

        result = container.run(lambda client: client.approve(approval_id, decided_by=decided_by))

        if json_enabled:
            output.print_json(result, pretty=pretty_enabled)
            return
        output.print_json(result, pretty=True)

    @approvals_app.command("deny")
    def deny(
        ctx: typer.Context,
        approval_id: str,
        decided_by: str | None = DECIDED_BY_OPT,
        json_output: bool = JSON_OPT,
        pretty: bool = PRETTY_OPT,
    ) -> None:
        container = ctx.obj
        if not isinstance(container, CliContainer):
            output.abort("internal error: invalid CLI context object")

        result = container.run(lambda client: client.deny(approval_id, decided_by=decided_by))

        pretty_enabled = bool(container.output_pretty or pretty)
        json_enabled = bool(container.output_json or json_output or pretty_enabled)
        if json_enabled:
            output.print_json(result, pretty=pretty_enabled)
            return
        output.print_json(result, pretty=True)


__all__ = ["register"]

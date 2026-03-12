from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, cast

import typer
from pydantic import ValidationError

from reflexor.cli import output
from reflexor.cli.client import CliTransportError
from reflexor.cli.commands._query_errors import print_query_error
from reflexor.cli.container import CliContainer
from reflexor.domain.models_event import Event

JSON_OPT = typer.Option(False, "--json", help="Output machine-readable JSON.")
PRETTY_OPT = typer.Option(False, "--pretty", help="Pretty-print JSON (implies --json).")
TYPE_OPT = typer.Option(..., "--type", help="Event type.")
SOURCE_OPT = typer.Option("cli", "--source", help="Event source.")
PAYLOAD_OPT = typer.Option(
    None,
    "--payload",
    help="Event payload as a JSON object string.",
)
PAYLOAD_FILE_OPT = typer.Option(
    None,
    "--payload-file",
    exists=True,
    dir_okay=False,
    readable=True,
    help="Load event payload from a JSON file (must contain an object).",
)
DEDUPE_KEY_OPT = typer.Option(None, "--dedupe-key", help="Optional dedupe key.")


def _parse_payload_json(raw: str) -> dict[str, object]:
    text = raw.strip()
    if not text:
        return {}

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("payload must be valid JSON") from exc

    if not isinstance(parsed, dict):
        raise ValueError("payload must be a JSON object")

    return cast(dict[str, object], parsed)


def _load_payload(*, payload: str | None, payload_file: Path | None) -> dict[str, object]:
    if payload is not None and payload_file is not None:
        raise ValueError("provide only one of --payload or --payload-file")

    if payload_file is not None:
        try:
            raw = payload_file.read_text(encoding="utf-8")
        except OSError as exc:
            raise ValueError(f"failed to read payload file: {payload_file}") from exc
        return _parse_payload_json(raw)

    if payload is None:
        return {}
    return _parse_payload_json(payload)


def _print_input_error(
    *,
    message: str,
    json_enabled: bool,
    pretty_enabled: bool,
    exit_code: int = 2,
) -> None:
    if json_enabled:
        output.print_json(
            {
                "ok": False,
                "error_code": "invalid_input",
                "message": message,
            },
            pretty=pretty_enabled,
        )
        raise typer.Exit(exit_code)
    output.abort(message, exit_code=exit_code)


def register(app: typer.Typer) -> None:
    @app.command("submit-event")
    def submit_event(
        ctx: typer.Context,
        event_type: str = TYPE_OPT,
        source: str = SOURCE_OPT,
        payload: str | None = PAYLOAD_OPT,
        payload_file: Path | None = PAYLOAD_FILE_OPT,
        dedupe_key: str | None = DEDUPE_KEY_OPT,
        json_output: bool = JSON_OPT,
        pretty: bool = PRETTY_OPT,
    ) -> None:
        """Submit an event (inline JSON or file payload)."""

        container = ctx.obj
        if not isinstance(container, CliContainer):
            output.abort("internal error: invalid CLI context object")

        pretty_enabled = bool(container.output_pretty or pretty)
        json_enabled = bool(container.output_json or json_output or pretty_enabled)

        normalized_source = source.strip()
        if not normalized_source:
            _print_input_error(
                message="source must be non-empty",
                json_enabled=json_enabled,
                pretty_enabled=pretty_enabled,
            )

        try:
            payload_dict = _load_payload(payload=payload, payload_file=payload_file)
        except ValueError as exc:
            _print_input_error(
                message=str(exc),
                json_enabled=json_enabled,
                pretty_enabled=pretty_enabled,
            )
            return

        received_at_ms = int(time.time() * 1000)

        try:
            event = Event.model_validate(
                {
                    "type": event_type,
                    "source": normalized_source,
                    "received_at_ms": received_at_ms,
                    "payload": payload_dict,
                    "dedupe_key": dedupe_key,
                },
                context={"max_payload_bytes": int(container.settings.max_event_payload_bytes)},
            )
        except ValidationError as exc:
            message = str(exc)
            errors = exc.errors()
            details: dict[str, Any] = {"errors": errors} if errors else {}
            if json_enabled:
                output.print_json(
                    {
                        "ok": False,
                        "error_code": "validation_error",
                        "message": message,
                        "details": details,
                    },
                    pretty=pretty_enabled,
                )
                raise typer.Exit(2) from None
            output.abort(message, exit_code=2)
            return

        try:
            result = container.run(lambda client: client.submit_event(event))
        except (KeyError, ValueError, CliTransportError) as exc:
            print_query_error(exc, json_enabled=json_enabled, pretty_enabled=pretty_enabled)
            return

        if json_enabled:
            output.print_json(result, pretty=pretty_enabled)
            return

        output.echo(f"event_id: {result.get('event_id')}")
        output.echo(f"run_id: {result.get('run_id')}")
        output.echo(f"duplicate: {result.get('duplicate')}")


__all__ = ["register"]

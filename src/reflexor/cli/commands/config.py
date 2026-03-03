from __future__ import annotations

import json
from urllib.parse import quote, urlsplit, urlunsplit

import typer

from reflexor.cli import output
from reflexor.cli.container import CliContainer
from reflexor.config import ReflexorSettings

JSON_OPT = typer.Option(False, "--json", help="Output machine-readable JSON.")
PRETTY_OPT = typer.Option(False, "--pretty", help="Pretty-print JSON (implies --json).")

_REDACTED = "<redacted>"


def _redact_url_password(raw: str) -> str:
    try:
        parts = urlsplit(raw)
    except ValueError:
        return raw

    if parts.password is None:
        return raw

    host = parts.hostname
    if not host:
        return raw

    rendered_host = host
    if ":" in rendered_host and not rendered_host.startswith("["):
        rendered_host = f"[{rendered_host}]"

    username = parts.username
    userinfo = ""
    if username:
        userinfo = quote(username, safe="")

    if userinfo:
        userinfo = f"{userinfo}:{_REDACTED}"
    else:
        userinfo = _REDACTED

    port = "" if parts.port is None else f":{parts.port}"
    netloc = f"{userinfo}@{rendered_host}{port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _redacted_settings_payload(settings: ReflexorSettings) -> dict[str, object]:
    data = settings.model_dump(mode="json")

    if settings.admin_api_key:
        data["admin_api_key"] = _REDACTED

    database_url = data.get("database_url")
    if isinstance(database_url, str):
        data["database_url"] = _redact_url_password(database_url)

    api_url = data.get("api_url")
    if isinstance(api_url, str):
        data["api_url"] = _redact_url_password(api_url)

    return data


def _stringify_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def register(app: typer.Typer) -> None:
    config_app = typer.Typer(help="Show effective configuration.")
    app.add_typer(config_app, name="config")

    @config_app.command("show")
    def show_config(
        ctx: typer.Context,
        json_output: bool = JSON_OPT,
        pretty: bool = PRETTY_OPT,
    ) -> None:
        container = ctx.obj
        if not isinstance(container, CliContainer):
            output.abort("internal error: invalid CLI context object")

        payload = _redacted_settings_payload(container.settings)

        pretty_enabled = bool(container.output_pretty or pretty)
        json_enabled = bool(container.output_json or json_output or pretty_enabled)
        if json_enabled:
            output.print_json(payload, pretty=pretty_enabled)
            return

        keys = [
            "profile",
            "dry_run",
            "enabled_scopes",
            "approval_required_scopes",
            "http_allowed_domains",
            "webhook_allowed_targets",
            "workspace_root",
            "database_url",
            "queue_backend",
            "queue_visibility_timeout_s",
        ]
        remaining = [key for key in payload.keys() if key not in set(keys)]
        keys.extend(sorted(remaining))

        rows: list[dict[str, object]] = [
            {"key": key, "value": _stringify_value(payload.get(key))} for key in keys
        ]
        output.print_table(
            rows,
            columns=[
                output.TableColumn("key", "KEY", max_width=40),
                output.TableColumn("value", "VALUE", max_width=120),
            ],
        )


__all__ = ["register"]

from __future__ import annotations

from urllib.parse import urlsplit

from pydantic import ValidationError

from reflexor.config import ReflexorSettings
from reflexor.domain.models_run_packet import RunPacket
from reflexor.replay.runner.types import ReplayError, ReplayMode
from reflexor.security.net_safety import normalize_hostname, validate_and_normalize_url
from reflexor.security.scopes import Scope


def _safe_default_enabled_scopes(scopes: set[str]) -> list[str]:
    safe: list[str] = []
    if Scope.FS_READ.value in scopes:
        safe.append(Scope.FS_READ.value)
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

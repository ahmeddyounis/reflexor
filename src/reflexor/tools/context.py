from __future__ import annotations

from reflexor.config import ReflexorSettings
from reflexor.observability.context import get_correlation_ids
from reflexor.security.secrets import SecretsProvider
from reflexor.tools.sdk import ToolContext


def tool_context_from_settings(
    settings: ReflexorSettings,
    *,
    timeout_s: int | None = None,
    correlation_ids: dict[str, str | None] | None = None,
    secrets_provider: SecretsProvider | None = None,
) -> ToolContext:
    """Build a `ToolContext` from runtime settings.

    Executors are expected to provide a secrets provider explicitly; settings should not contain
    raw secrets.
    """

    return ToolContext(
        workspace_root=settings.workspace_root,
        dry_run=settings.dry_run,
        timeout_s=60 if timeout_s is None else timeout_s,
        correlation_ids=get_correlation_ids() if correlation_ids is None else dict(correlation_ids),
        secrets_provider=secrets_provider,
    )

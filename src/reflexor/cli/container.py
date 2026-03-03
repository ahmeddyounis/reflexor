"""CLI composition root.

Clean Architecture:
- The CLI is an outer interface layer (like the API). It should remain thin.
- Command handlers must not access the ORM directly; they should call application services
  or a client abstraction.
- This module selects a CLI client implementation:
  - If `REFLEXOR_API_URL` is set, use `ApiClient` (HTTP -> FastAPI).
  - Otherwise use `LocalClient` (in-process application services).
- This module wires CLI dependencies while keeping command handlers decoupled from infra.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from reflexor.cli.client import ApiClient, CliClient, LocalClient
from reflexor.config import ReflexorSettings, get_settings


def build_api_client(settings: ReflexorSettings) -> ApiClient:
    if settings.api_url is None:
        raise ValueError("api_url is required to build ApiClient")
    return ApiClient(base_url=settings.api_url, admin_api_key=settings.admin_api_key)


def build_local_client(settings: ReflexorSettings) -> LocalClient:
    # Import lazily to avoid importing DB/infra on CLI help and simple commands.
    from reflexor.api.container import AppContainer

    app = AppContainer.build(settings=settings)
    return LocalClient(
        submitter=app.submit_events,
        run_queries=app.run_queries,
        task_queries=app.task_queries,
        approval_commands=app.approval_commands,
        tool_registry=app.tool_registry,
    )


def build_cli_client(
    settings: ReflexorSettings,
    *,
    local_factory: Callable[[ReflexorSettings], CliClient] = build_local_client,
    api_factory: Callable[[ReflexorSettings], CliClient] = build_api_client,
) -> CliClient:
    if settings.api_url:
        return api_factory(settings)
    return local_factory(settings)


@dataclass(slots=True)
class CliContainer:
    """Dependencies used by CLI commands."""

    settings: ReflexorSettings
    output_json: bool = False
    output_pretty: bool = False
    _client: CliClient | None = field(default=None, init=False, repr=False)
    _client_factory: Callable[[ReflexorSettings], CliClient] = field(
        default=build_cli_client, repr=False
    )

    def get_client(self) -> CliClient:
        if self._client is None:
            self._client = self._client_factory(self.settings)
        return self._client

    @classmethod
    def build(
        cls,
        *,
        settings: ReflexorSettings | None = None,
        client: CliClient | None = None,
        client_factory: Callable[[ReflexorSettings], CliClient] | None = None,
    ) -> CliContainer:
        effective_settings = get_settings() if settings is None else settings
        container = cls(settings=effective_settings)
        if client_factory is not None:
            container._client_factory = client_factory
        if client is not None:
            container._client = client
        return container


__all__ = [
    "CliContainer",
    "build_api_client",
    "build_cli_client",
    "build_local_client",
]

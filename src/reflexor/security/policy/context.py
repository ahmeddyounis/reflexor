"""Policy evaluation inputs (narrow, DI-friendly).

Clean Architecture:
- Policy code may depend on `reflexor.config`, `reflexor.domain`, and `reflexor.security.*`.
- Policy code may depend on tool boundary contracts (`reflexor.tools.sdk`) but must not import
  concrete tool implementations (`reflexor.tools.impl.*`).
- Forbidden: outer layers/frameworks (FastAPI, SQLAlchemy, queue/worker, CLI, etc.).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel

from reflexor.config import ReflexorSettings
from reflexor.tools.sdk import Tool, ToolManifest


@dataclass(frozen=True, slots=True)
class PolicyAllowlists:
    http_allowed_domains: tuple[str, ...]
    webhook_allowed_targets: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PolicyLimits:
    max_event_payload_bytes: int
    max_tool_output_bytes: int
    max_run_packet_bytes: int


@dataclass(frozen=True, slots=True)
class PolicyContext:
    profile: Literal["dev", "prod"]
    dry_run: bool

    enabled_scopes: tuple[str, ...]
    approval_required_scopes: tuple[str, ...]
    approval_required_domains: tuple[str, ...]
    approval_required_payload_keywords: tuple[str, ...]

    allowlists: PolicyAllowlists
    workspace_root: Path
    limits: PolicyLimits

    @classmethod
    def from_settings(cls, settings: ReflexorSettings) -> PolicyContext:
        return cls(
            profile=settings.profile,
            dry_run=settings.dry_run,
            enabled_scopes=tuple(settings.enabled_scopes),
            approval_required_scopes=tuple(settings.approval_required_scopes),
            approval_required_domains=tuple(settings.approval_required_domains),
            approval_required_payload_keywords=tuple(settings.approval_required_payload_keywords),
            allowlists=PolicyAllowlists(
                http_allowed_domains=tuple(settings.http_allowed_domains),
                webhook_allowed_targets=tuple(settings.webhook_allowed_targets),
            ),
            workspace_root=settings.workspace_root,
            limits=PolicyLimits(
                max_event_payload_bytes=settings.max_event_payload_bytes,
                max_tool_output_bytes=settings.max_tool_output_bytes,
                max_run_packet_bytes=settings.max_run_packet_bytes,
            ),
        )


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """Narrow tool description for policy evaluation."""

    tool_name: str
    manifest: ToolManifest
    args_model: type[BaseModel]


class ToolCatalog(Protocol):
    """Minimal view of a tool registry for policy adapters."""

    def get(self, name: str) -> Tool: ...

    def list_manifests(self) -> list[ToolManifest]: ...


def tool_spec_from_tool(tool: Tool) -> ToolSpec:
    return ToolSpec(
        tool_name=tool.manifest.name,
        manifest=tool.manifest,
        args_model=tool.ArgsModel,
    )


def tool_spec_from_catalog(catalog: ToolCatalog, *, tool_name: str) -> ToolSpec:
    return tool_spec_from_tool(catalog.get(tool_name))


def list_tool_specs(catalog: ToolCatalog) -> list[ToolSpec]:
    return [tool_spec_from_catalog(catalog, tool_name=m.name) for m in catalog.list_manifests()]

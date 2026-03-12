from __future__ import annotations

import math
from pathlib import Path

import pytest

from reflexor.config import ReflexorSettings
from reflexor.observability.context import correlation_context
from reflexor.security.secrets import EnvSecretsProvider
from reflexor.tools.context import tool_context_from_settings
from reflexor.tools.sdk import ToolContext


def test_tool_context_can_be_constructed_from_settings(tmp_path: Path) -> None:
    settings = ReflexorSettings(workspace_root=tmp_path, dry_run=True)
    ctx = tool_context_from_settings(settings, timeout_s=7)

    assert ctx.workspace_root.resolve(strict=False) == tmp_path.resolve(strict=False)
    assert ctx.dry_run is True
    assert ctx.timeout_s == 7
    assert ctx.secrets_provider is None


def test_tool_context_snapshots_correlation_ids(tmp_path: Path) -> None:
    settings = ReflexorSettings(workspace_root=tmp_path, dry_run=True)

    provider = EnvSecretsProvider()
    with correlation_context(
        event_id="evt_123",
        run_id="run_123",
        task_id="task_123",
        tool_call_id="call_123",
    ):
        ctx = tool_context_from_settings(settings, timeout_s=3, secrets_provider=provider)

    assert ctx.correlation_ids == {
        "event_id": "evt_123",
        "run_id": "run_123",
        "task_id": "task_123",
        "tool_call_id": "call_123",
    }
    assert ctx.secrets_provider is provider


def test_tool_context_rejects_relative_workspace_root() -> None:
    with pytest.raises(ValueError, match="workspace_root must be an absolute path"):
        ToolContext(workspace_root=Path("relative"))


def test_tool_context_rejects_non_positive_timeout(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="timeout_s must be > 0"):
        ToolContext(workspace_root=tmp_path, timeout_s=0)


@pytest.mark.parametrize("timeout_s", [math.nan, math.inf])
def test_tool_context_rejects_non_finite_timeout(tmp_path: Path, timeout_s: float) -> None:
    with pytest.raises(ValueError, match="timeout_s must be finite"):
        ToolContext(workspace_root=tmp_path, timeout_s=timeout_s)

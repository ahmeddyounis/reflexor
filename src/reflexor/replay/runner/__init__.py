"""Deterministic replay runner (safe by default).

This package provides a small helper to replay an exported run packet locally without
performing any real side effects (network/filesystem/webhook).

Replay modes:
- `dry_run_no_tools`: persist a replay run packet but never execute tools.
- `mock_tools_recorded`: execute tool calls using mock tools that return recorded ToolResults.
- `mock_tools_success`: execute tool calls using always-ok mock tools.

The runner enforces `dry_run=True` regardless of settings profile.
"""

from __future__ import annotations

from reflexor.replay.runner.core import ReplayRunner
from reflexor.replay.runner.types import ReplayError, ReplayMode, ReplayOutcome

__all__ = ["ReplayError", "ReplayMode", "ReplayOutcome", "ReplayRunner"]

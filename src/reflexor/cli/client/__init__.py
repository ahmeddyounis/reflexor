"""CLI client abstractions (DIP).

The CLI can operate in two modes:
- Local mode: direct in-process calls via application services, repos/UoW, and the queue.
- API mode: remote calls to the FastAPI service via HTTP.

Commands should depend on the `CliClient` protocol and avoid direct ORM/database access.
"""

from __future__ import annotations

from reflexor.cli.client.api import ApiClient
from reflexor.cli.client.local import LocalClient
from reflexor.cli.client.protocol import CliClient, ReplayModeStr

__all__ = ["ApiClient", "CliClient", "LocalClient", "ReplayModeStr"]

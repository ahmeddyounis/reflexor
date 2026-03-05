"""Orchestration engine scaffolding.

The orchestrator engine is responsible for coordinating reflex decisions, planning, queueing, and
execution while keeping dependencies pointed inward (Clean Architecture).

Clean Architecture:
- Orchestrator is application-layer code.
- Engine code may depend on `reflexor.domain`, `reflexor.config`, queue interface/contracts, and
  tool boundary types/registries.
- Forbidden: FastAPI/SQLAlchemy/httpx/worker/API/CLI imports.
"""

from __future__ import annotations

from reflexor.orchestrator.engine.core import OrchestratorEngine
from reflexor.orchestrator.sinks import NoopRunPacketSink, RunPacketSink

__all__ = ["NoopRunPacketSink", "OrchestratorEngine", "RunPacketSink"]

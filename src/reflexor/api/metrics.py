"""API metrics shim.

Metric definitions live in `reflexor.observability.metrics` so they can be shared by
API/worker/orchestrator without scattering Prometheus globals across layers.
"""

from __future__ import annotations

from reflexor.observability.metrics import ReflexorMetrics as ApiMetrics

__all__ = ["ApiMetrics"]


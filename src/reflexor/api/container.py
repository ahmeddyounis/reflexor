"""Deprecated shim for `reflexor.bootstrap.container` (planned removal in 2.0.0).

`AppContainer` now lives in `reflexor.bootstrap.container` so non-API entrypoints (CLI/worker)
can reuse the same wiring without importing the FastAPI layer.
"""

from __future__ import annotations

from reflexor.bootstrap.container import AppContainer, RepoProviders

__all__ = ["AppContainer", "RepoProviders"]

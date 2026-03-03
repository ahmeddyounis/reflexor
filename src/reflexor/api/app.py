"""ASGI application entrypoint.

This module exposes the FastAPI `app` used by ASGI servers (e.g. uvicorn).

Clean Architecture:
- API is a runtime/outer interface layer.
- Keep imports minimal and avoid importing infrastructure adapters at import time.
"""

from __future__ import annotations

from fastapi import FastAPI

from reflexor.version import __version__

app = FastAPI(title="Reflexor", version=__version__)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


__all__ = ["app"]

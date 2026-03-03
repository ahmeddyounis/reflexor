from __future__ import annotations

from fastapi import APIRouter

from reflexor.version import __version__

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


__all__ = ["router"]

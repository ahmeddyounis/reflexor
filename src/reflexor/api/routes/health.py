from __future__ import annotations

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from reflexor.api.deps import ContainerDep
from reflexor.version import __version__

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz(container: ContainerDep) -> JSONResponse:
    time_ms = int(container.orchestrator_engine.clock.now_ms())
    db_ok = await container.ping_db(timeout_s=1.0)

    payload: dict[str, object] = {
        "ok": bool(db_ok),
        "version": __version__,
        "profile": container.settings.profile,
        "time_ms": time_ms,
        "db_ok": bool(db_ok),
    }
    status_code = status.HTTP_200_OK if db_ok else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(status_code=status_code, content=payload)


__all__ = ["router"]

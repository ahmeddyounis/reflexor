from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from reflexor.api.auth import require_admin
from reflexor.api.deps import ContainerDep

router = APIRouter(tags=["metrics"], dependencies=[Depends(require_admin)])


@router.get("/metrics")
async def metrics(container: ContainerDep) -> Response:
    pending = await container.count_pending_approvals(timeout_s=1.0)
    if pending is None:
        container.metrics.metrics_refresh_failures_total.labels(
            metric="approvals_pending_total"
        ).inc()
    else:
        container.metrics.approvals_pending_total.set(pending)

    payload = generate_latest(container.metrics.registry)
    response = Response(content=payload, media_type=CONTENT_TYPE_LATEST)
    response.headers["Cache-Control"] = "no-store"
    return response


__all__ = ["router"]

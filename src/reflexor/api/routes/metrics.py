from __future__ import annotations

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from reflexor.api.deps import ContainerDep

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
async def metrics(container: ContainerDep) -> Response:
    pending = await container.count_pending_approvals(timeout_s=1.0)
    if pending is None:
        container.metrics.approvals_pending_total.set(-1)
    else:
        container.metrics.approvals_pending_total.set(pending)

    payload = generate_latest(container.metrics.registry)
    return Response(content=payload, media_type=CONTENT_TYPE_LATEST)


__all__ = ["router"]

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Response, status

from reflexor.api.auth import require_events_access
from reflexor.api.deps import ContainerDep, EventSubmitterDep, QueryServiceDep
from reflexor.api.schemas import ErrorResponse, SubmitEventRequest, SubmitEventResponse
from reflexor.domain.models_event import Event

router = APIRouter(
    prefix="/v1/events", tags=["events"], dependencies=[Depends(require_events_access)]
)
compat_router = APIRouter(
    prefix="/events", tags=["events"], dependencies=[Depends(require_events_access)]
)


async def submit_event(
    submitter: EventSubmitterDep,
    container: ContainerDep,
    request: SubmitEventRequest,
    response: Response,
) -> SubmitEventResponse:
    start_s = time.perf_counter()
    received_at_ms = request.received_at_ms
    if received_at_ms is None:
        received_at_ms = int(container.orchestrator_engine.clock.now_ms())

    event = Event.model_validate(
        {
            "type": request.type,
            "source": request.source,
            "received_at_ms": received_at_ms,
            "payload": request.payload,
            "dedupe_key": request.dedupe_key,
        },
        context={"max_payload_bytes": int(container.settings.max_event_payload_bytes)},
    )

    outcome = await submitter.submit_event(event)
    if outcome.duplicate:
        response.status_code = status.HTTP_200_OK

    container.metrics.events_received_total.inc()
    container.metrics.event_ingest_latency_seconds.observe(time.perf_counter() - start_s)

    return SubmitEventResponse(
        event_id=outcome.event_id,
        run_id=outcome.run_id,
        duplicate=outcome.duplicate,
    )


async def list_events(_queries: QueryServiceDep) -> dict[str, object]:
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="not implemented")


for _r in (router, compat_router):
    _r.add_api_route(
        "",
        submit_event,
        methods=["POST"],
        response_model=SubmitEventResponse,
        status_code=status.HTTP_202_ACCEPTED,
        responses={400: {"model": ErrorResponse}, 413: {"model": ErrorResponse}},
    )
    _r.add_api_route("", list_events, methods=["GET"])


__all__ = ["compat_router", "router"]

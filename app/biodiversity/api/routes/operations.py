"""Operation status and SSE routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse

from app.biodiversity.api.dependencies import application
from app.biodiversity.api.errors import invalid_operation, not_found
from app.biodiversity.api.schemas import OperationView
from app.biodiversity.api.services.operations import Phase4Application
from app.biodiversity.api.sse import operation_event_stream


router = APIRouter(tags=["operations"])


@router.get("/operations/{operation_id}", response_model=OperationView)
async def get_operation(
    operation_id: str,
    service: Phase4Application = Depends(application),
) -> OperationView:
    operation = service.operation(operation_id)
    if operation is None:
        raise not_found("operation")
    return operation


@router.get("/operations/{operation_id}/events")
async def get_operation_events(
    operation_id: str,
    request: Request,
    after_sequence: int = Query(default=0, ge=0),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    service: Phase4Application = Depends(application),
) -> StreamingResponse:
    operation = service.operation(operation_id)
    if operation is None:
        raise not_found("operation")
    sequence = after_sequence
    if last_event_id is not None:
        try:
            sequence = max(sequence, int(last_event_id))
        except ValueError:
            raise invalid_operation("Last-Event-ID must be a non-negative integer.") from None
        if sequence < 0:
            raise invalid_operation("Last-Event-ID must be a non-negative integer.")
    if sequence > operation.last_event_sequence:
        raise invalid_operation(
            "The requested event sequence is newer than this operation."
        )
    return StreamingResponse(
        operation_event_stream(
            service,
            operation_id=operation_id,
            after_sequence=sequence,
            request=request,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

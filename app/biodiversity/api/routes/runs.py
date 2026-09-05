"""Run, history, checkpoint, HITL, replay, fork, and comparison routes."""

from __future__ import annotations

import asyncio
from uuid import uuid4

from fastapi import APIRouter, Depends, Query, status

from app.biodiversity.api.dependencies import application
from app.biodiversity.api.errors import (
    conflict,
    forbidden,
    invalid_operation,
    not_found,
    rate_limited,
    service_busy,
    unavailable,
)
from app.biodiversity.api.schemas import (
    ComparisonView,
    CreateRunRequest,
    EvidenceView,
    FinalPlanView,
    ForkRunRequest,
    HistoryView,
    MapEvidenceView,
    OperationAccepted,
    ReplayRunRequest,
    RouteGeometryView,
    RouteOptionsView,
    ResumeRunRequest,
    RunDetail,
    RunSummary,
)
from app.biodiversity.api.services.operations import Phase4Application
from app.biodiversity.run_models import StateView


router = APIRouter(tags=["runs"])


def _read_error(exc: ValueError) -> Exception:
    text = str(exc)
    if "No biodiversity run exists" in text:
        return not_found("run")
    if "does not exist in thread" in text:
        return not_found("checkpoint")
    return invalid_operation("The requested checkpoint operation is invalid.")


async def _existing(service: Phase4Application, thread_id: str) -> None:
    try:
        exists = await asyncio.to_thread(service.thread_exists, thread_id)
    except ValueError as exc:
        raise _read_error(exc) from None
    if not exists:
        raise not_found("run")


async def _submit(
    service: Phase4Application,
    *,
    thread_id: str,
    kind: str,
    payload: dict,
    profile: object,
) -> OperationAccepted:
    try:
        return await service.coordinator.submit(
            thread_id=thread_id,
            kind=kind,
            payload=payload,
            profile=profile,
        )
    except RuntimeError as exc:
        if str(exc) == "same_thread_mutation_conflict":
            raise conflict("Another mutation is already running for this thread.") from None
        if str(exc) == "thread_already_exists":
            raise conflict("The requested thread already exists.") from None
        if str(exc) == "demo_rate_limit":
            raise rate_limited() from None
        if str(exc) in {
            "operation_capacity",
            "demo_run_capacity",
            "demo_storage_capacity",
        }:
            raise service_busy() from None
        raise


@router.post(
    "/runs",
    response_model=OperationAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_run(
    body: CreateRunRequest,
    service: Phase4Application = Depends(application),
) -> OperationAccepted:
    thread_id = body.thread_id or f"expedition-{uuid4().hex[:12]}"
    if await asyncio.to_thread(service.thread_exists, thread_id):
        raise conflict("The requested thread already exists.")
    try:
        profile = service.start_profile(body.data_mode, body.model_mode)
    except PermissionError:
        raise forbidden("This run mode is not enabled on this deployment.") from None
    except RuntimeError:
        raise unavailable() from None
    return await _submit(
        service,
        thread_id=thread_id,
        kind="start",
        payload={"request": body.request},
        profile=profile,
    )


@router.get("/runs", response_model=list[RunSummary])
async def list_runs(
    service: Phase4Application = Depends(application),
) -> list[RunSummary]:
    return await asyncio.to_thread(service.list_runs)


@router.get("/runs/{thread_id}", response_model=RunDetail)
async def get_run(
    thread_id: str,
    service: Phase4Application = Depends(application),
) -> RunDetail:
    detail = await asyncio.to_thread(service.run_detail, thread_id)
    if detail is None:
        raise not_found("run")
    return detail


@router.get("/runs/{thread_id}/history", response_model=HistoryView)
async def get_history(
    thread_id: str,
    service: Phase4Application = Depends(application),
) -> HistoryView:
    try:
        return await asyncio.to_thread(service.history, thread_id)
    except ValueError as exc:
        raise _read_error(exc) from None


@router.get(
    "/runs/{thread_id}/checkpoints/{checkpoint_id}/state",
    response_model=StateView,
)
async def get_checkpoint_state(
    thread_id: str,
    checkpoint_id: str,
    node_id: str = Query(min_length=1),
    graph_step: int = Query(),
    service: Phase4Application = Depends(application),
) -> StateView:
    try:
        return await asyncio.to_thread(
            service.runtime.reader().state_view,
            thread_id=thread_id,
            node_id=node_id,
            graph_step=graph_step,
            checkpoint_id=checkpoint_id,
        )
    except ValueError as exc:
        raise _read_error(exc) from None


@router.get(
    "/runs/{thread_id}/checkpoints/{checkpoint_id}/evidence",
    response_model=EvidenceView,
)
async def get_checkpoint_evidence(
    thread_id: str,
    checkpoint_id: str,
    service: Phase4Application = Depends(application),
) -> EvidenceView:
    try:
        return await asyncio.to_thread(
            service.views.evidence,
            service.runtime.reader(),
            thread_id=thread_id,
            checkpoint_id=checkpoint_id,
        )
    except ValueError as exc:
        raise _read_error(exc) from None


@router.get(
    "/runs/{thread_id}/checkpoints/{checkpoint_id}/plan",
    response_model=FinalPlanView | None,
)
async def get_checkpoint_plan(
    thread_id: str,
    checkpoint_id: str,
    service: Phase4Application = Depends(application),
) -> FinalPlanView | None:
    try:
        return await asyncio.to_thread(
            service.views.checkpoint_plan,
            service.runtime.reader(),
            thread_id=thread_id,
            checkpoint_id=checkpoint_id,
        )
    except ValueError as exc:
        raise _read_error(exc) from None


@router.get(
    "/runs/{thread_id}/checkpoints/{checkpoint_id}/map",
    response_model=MapEvidenceView,
)
async def get_checkpoint_map(
    thread_id: str,
    checkpoint_id: str,
    service: Phase4Application = Depends(application),
) -> MapEvidenceView:
    try:
        return await asyncio.to_thread(
            service.views.map_evidence,
            service.runtime.reader(),
            thread_id=thread_id,
            checkpoint_id=checkpoint_id,
        )
    except ValueError as exc:
        raise _read_error(exc) from None


@router.get(
    "/runs/{thread_id}/checkpoints/{checkpoint_id}/routes",
    response_model=RouteOptionsView,
)
async def get_checkpoint_routes(
    thread_id: str,
    checkpoint_id: str,
    service: Phase4Application = Depends(application),
) -> RouteOptionsView:
    try:
        return await asyncio.to_thread(
            service.route_options,
            thread_id=thread_id,
            checkpoint_id=checkpoint_id,
        )
    except ValueError as exc:
        raise _read_error(exc) from None


@router.get(
    "/runs/{thread_id}/checkpoints/{checkpoint_id}/route-geometry/{route_geometry_reference}",
    response_model=RouteGeometryView,
)
async def get_checkpoint_route_geometry(
    thread_id: str,
    checkpoint_id: str,
    route_geometry_reference: str,
    service: Phase4Application = Depends(application),
) -> RouteGeometryView:
    try:
        return await asyncio.to_thread(
            service.route_geometry,
            thread_id=thread_id,
            checkpoint_id=checkpoint_id,
            route_geometry_reference=route_geometry_reference,
        )
    except PermissionError:
        raise forbidden(
            "Public route geometry is available only for the planned fixture origin."
        ) from None
    except ValueError as exc:
        if str(exc) == "route_geometry_unavailable":
            raise not_found("route geometry") from None
        raise _read_error(exc) from None


@router.post(
    "/runs/{thread_id}/resume",
    response_model=OperationAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def resume_run(
    thread_id: str,
    body: ResumeRunRequest,
    service: Phase4Application = Depends(application),
) -> OperationAccepted:
    await _existing(service, thread_id)
    try:
        profile = await asyncio.to_thread(service.thread_profile, thread_id)
        await asyncio.to_thread(
            service.validate_resume,
            thread_id=thread_id,
            request=body,
        )
    except PermissionError:
        raise forbidden("This run mode is not enabled on this deployment.") from None
    except RuntimeError as exc:
        if str(exc) == "ambiguous_resume":
            raise conflict(
                "Multiple executions are waiting; select an exact checkpoint or branch."
            ) from None
        raise unavailable() from None
    except ValueError as exc:
        raise _read_error(exc) from None
    return await _submit(
        service,
        thread_id=thread_id,
        kind="resume",
        payload={
            "resume": body.manager_payload(),
            "checkpoint_id": body.checkpoint_id,
            "branch_id": body.branch_id,
        },
        profile=profile,
    )


@router.post(
    "/runs/{thread_id}/replay",
    response_model=OperationAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def replay_run(
    thread_id: str,
    body: ReplayRunRequest,
    service: Phase4Application = Depends(application),
) -> OperationAccepted:
    await _existing(service, thread_id)
    try:
        profile = await asyncio.to_thread(service.thread_profile, thread_id)
        await asyncio.to_thread(
            service.validate_replay,
            thread_id=thread_id,
            checkpoint_id=body.checkpoint_id,
        )
    except PermissionError:
        raise forbidden("This run mode is not enabled on this deployment.") from None
    except RuntimeError:
        raise unavailable() from None
    except ValueError as exc:
        if str(exc) == "terminal_checkpoint":
            raise invalid_operation("A terminal checkpoint cannot be replayed.") from None
        raise _read_error(exc) from None
    return await _submit(
        service,
        thread_id=thread_id,
        kind="replay",
        payload={"checkpoint_id": body.checkpoint_id},
        profile=profile,
    )


@router.post(
    "/runs/{thread_id}/fork",
    response_model=OperationAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def fork_run(
    thread_id: str,
    body: ForkRunRequest,
    service: Phase4Application = Depends(application),
) -> OperationAccepted:
    await _existing(service, thread_id)
    try:
        profile = await asyncio.to_thread(service.thread_profile, thread_id)
        await asyncio.to_thread(
            service.validate_fork,
            thread_id=thread_id,
            request=body,
        )
    except PermissionError:
        raise forbidden("This run mode is not enabled on this deployment.") from None
    except RuntimeError:
        raise unavailable() from None
    except ValueError as exc:
        if "do not change" in str(exc):
            raise invalid_operation(
                "Fork updates must change at least one allowed field; use replay otherwise."
            ) from None
        raise _read_error(exc) from None
    return await _submit(
        service,
        thread_id=thread_id,
        kind="fork",
        payload={
            "checkpoint_id": body.checkpoint_id,
            "updates": body.updates.model_dump(mode="json", exclude_none=True),
            "branch_label": body.branch_label,
        },
        profile=profile,
    )


@router.get("/runs/{thread_id}/compare", response_model=ComparisonView)
async def compare_runs(
    thread_id: str,
    checkpoint_a: str = Query(min_length=1),
    checkpoint_b: str = Query(min_length=1),
    service: Phase4Application = Depends(application),
) -> ComparisonView:
    try:
        return await asyncio.to_thread(
            service.compare,
            thread_id=thread_id,
            checkpoint_a=checkpoint_a,
            checkpoint_b=checkpoint_b,
        )
    except ValueError as exc:
        raise _read_error(exc) from None

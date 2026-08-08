from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status

from app.dependencies import get_activity_service
from app.models.activity import ActivityEventCreate, ActivityRecord, QueuedActivityEvent
from app.models.common import SuccessResponse
from app.models.elderly import ElderlyId
from app.models.health import IdempotencyKey
from app.services.activity import ActivityEventService, derive_activity_event_id
from app.services.elderly import ElderlyProfileNotFound
from app.services.auth import Principal, get_telemetry_principal, require_relationship_permission


router = APIRouter(prefix="/api/activity", tags=["activity"])


@router.post("", response_model=SuccessResponse[QueuedActivityEvent], status_code=status.HTTP_202_ACCEPTED)
async def queue_activity_event(
    request: ActivityEventCreate,
    service: Annotated[ActivityEventService, Depends(get_activity_service)],
    idempotency_key: Annotated[IdempotencyKey, Header(alias="Idempotency-Key")],
    _: Annotated[Principal, Depends(get_telemetry_principal)],
) -> SuccessResponse[QueuedActivityEvent]:
    try:
        event = await service.queue_event(
            request, derive_activity_event_id(request.elderly_id, idempotency_key), idempotency_key
        )
    except ElderlyProfileNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return SuccessResponse(
        message="Activity event queued successfully",
        data=QueuedActivityEvent(event_id=event.event_id, elderly_id=event.elderly_id),
    )


@router.get("/{elderly_id}", response_model=SuccessResponse[list[ActivityRecord]])
async def list_activity_history(
    elderly_id: ElderlyId,
    service: Annotated[ActivityEventService, Depends(get_activity_service)],
    _: Annotated[Principal, require_relationship_permission("read_activity")],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> SuccessResponse[list[ActivityRecord]]:
    return SuccessResponse(
        message="Activity history retrieved successfully",
        data=await service.list_activity(elderly_id, limit, offset),
    )

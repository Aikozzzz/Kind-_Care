from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status

from app.dependencies import get_device_service
from app.models.common import SuccessResponse
from app.models.device import DeviceEventRecord, DeviceHeartbeatCreate, QueuedDeviceEvent
from app.models.elderly import ElderlyId
from app.models.health import IdempotencyKey
from app.services.device import DeviceEventService, derive_device_event_id
from app.services.elderly import ElderlyProfileNotFound


router = APIRouter(prefix="/api/device-status", tags=["device-status"])


@router.post("", response_model=SuccessResponse[QueuedDeviceEvent], status_code=status.HTTP_202_ACCEPTED)
async def queue_device_heartbeat(
    request: DeviceHeartbeatCreate,
    service: Annotated[DeviceEventService, Depends(get_device_service)],
    idempotency_key: Annotated[IdempotencyKey, Header(alias="Idempotency-Key")],
) -> SuccessResponse[QueuedDeviceEvent]:
    try:
        event = await service.queue_event(
            request, derive_device_event_id(request.elderly_id, idempotency_key), idempotency_key
        )
    except ElderlyProfileNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return SuccessResponse(
        message="Device heartbeat queued successfully",
        data=QueuedDeviceEvent(event_id=event.event_id, elderly_id=event.elderly_id),
    )


@router.get("/{elderly_id}", response_model=SuccessResponse[list[DeviceEventRecord]])
async def list_device_history(
    elderly_id: ElderlyId,
    service: Annotated[DeviceEventService, Depends(get_device_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> SuccessResponse[list[DeviceEventRecord]]:
    return SuccessResponse(
        message="Device history retrieved successfully",
        data=await service.list_events(elderly_id, limit, offset),
    )

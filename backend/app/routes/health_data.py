from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status

from app.dependencies import get_health_service
from app.models.common import SuccessResponse
from app.models.elderly import ElderlyId
from app.models.health import (
    HealthEventCreate,
    HealthRecord,
    IdempotencyKey,
    QueuedHealthEvent,
)
from app.services.elderly import ElderlyProfileNotFound
from app.services.health import HealthEventService, derive_health_event_id
from app.services.auth import Principal, get_telemetry_principal, require_relationship_permission


router = APIRouter(prefix="/api/health", tags=["health"])
ServiceDependency = Annotated[HealthEventService, Depends(get_health_service)]
TelemetryDependency = Annotated[Principal, Depends(get_telemetry_principal)]
ReadDependency = Annotated[Principal, require_relationship_permission("read_health")]
IdempotencyKeyHeader = Annotated[
    IdempotencyKey,
    Header(alias="Idempotency-Key"),
]


@router.post(
    "",
    response_model=SuccessResponse[QueuedHealthEvent],
    status_code=status.HTTP_202_ACCEPTED,
)
async def queue_health_event(
    request: HealthEventCreate,
    service: ServiceDependency,
    idempotency_key: IdempotencyKeyHeader,
    _: TelemetryDependency,
) -> SuccessResponse[QueuedHealthEvent]:
    event_id = derive_health_event_id(request.elderly_id, idempotency_key)
    try:
        event = await service.queue_event(request, event_id, idempotency_key)
    except ElderlyProfileNotFound as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    return SuccessResponse(
        message="Health event queued successfully",
        data=QueuedHealthEvent(
            event_id=event.event_id,
            elderly_id=event.elderly_id,
        ),
    )


@router.get("/{elderly_id}", response_model=SuccessResponse[list[HealthRecord]])
async def list_health_history(
    elderly_id: ElderlyId,
    service: ServiceDependency,
    _: ReadDependency,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> SuccessResponse[list[HealthRecord]]:
    records = await service.list_health(elderly_id, limit, offset)
    return SuccessResponse(
        message="Health history retrieved successfully",
        data=records,
    )

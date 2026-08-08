from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from starlette.requests import HTTPConnection

from app.dependencies import get_database, get_reminder_service
from app.models.common import SuccessResponse
from app.models.elderly import ElderlyId
from app.models.health import IdempotencyKey
from app.models.reminder import (
    ReminderCreate,
    ReminderRecord,
    ReminderStatus,
    ReminderStatusUpdate,
)
from app.services.elderly import ElderlyProfileNotFound
from app.services.reminder import ReminderConflict, ReminderNotFound, ReminderService
from app.services.auth import (
    Principal,
    RelationshipDenied,
    authorize_relationship,
    get_current_principal,
    require_admin_access,
    require_relationship_permission,
    get_telemetry_principal,
)
from pymongo.asynchronous.database import AsyncDatabase


router = APIRouter(prefix="/api/reminders", tags=["reminders"])
ServiceDependency = Annotated[ReminderService, Depends(get_reminder_service)]
DatabaseDependency = Annotated[AsyncDatabase, Depends(get_database)]


@router.post("", response_model=SuccessResponse[ReminderRecord], status_code=201)
async def create_reminder(
    request: ReminderCreate,
    service: ServiceDependency,
    idempotency_key: Annotated[IdempotencyKey, Header(alias="Idempotency-Key")],
    principal: Annotated[Principal, Depends(get_telemetry_principal)],
) -> SuccessResponse[ReminderRecord]:
    try:
        reminder = await service.create(request, idempotency_key)
    except ElderlyProfileNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ReminderConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return SuccessResponse(message="Reminder created successfully", data=reminder)


@router.get("/{elderly_id}", response_model=SuccessResponse[list[ReminderRecord]])
async def list_reminders(
    elderly_id: ElderlyId,
    service: ServiceDependency,
    _: Annotated[Principal, require_relationship_permission("read_reminders")],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
    reminder_status: Annotated[ReminderStatus | None, Query(alias="status")] = None,
) -> SuccessResponse[list[ReminderRecord]]:
    records = await service.list(elderly_id, limit, offset, reminder_status)
    return SuccessResponse(message="Reminders retrieved successfully", data=records)


@router.patch("/{reminder_id}", response_model=SuccessResponse[ReminderRecord])
async def mark_reminder_taken(
    reminder_id: str,
    request: ReminderStatusUpdate,
    service: ServiceDependency,
    principal: Annotated[Principal, Depends(get_current_principal)],
    connection: HTTPConnection,
) -> SuccessResponse[ReminderRecord]:
    if not (principal.is_admin or principal.is_service):
        try:
            await authorize_relationship(
                connection.app.state.database,
                principal,
                request.elderly_id,
                "mark_reminder_taken",
            )
        except RelationshipDenied as error:
            raise HTTPException(status_code=404, detail="Resident was not found") from error
    try:
        reminder = await service.mark_taken(reminder_id, request.elderly_id)
    except (ReminderNotFound, ElderlyProfileNotFound) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ReminderConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return SuccessResponse(message="Reminder marked taken", data=reminder)

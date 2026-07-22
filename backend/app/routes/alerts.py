from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.dependencies import get_alert_service
from app.models.common import SuccessResponse
from app.models.elderly import ElderlyId
from app.models.health import AlertRecord, AlertSeverity, AlertStatus, AlertStatusUpdate
from app.services.alerts import AlertConflict, AlertNotFound, AlertService


router = APIRouter(prefix="/api/alerts", tags=["alerts"])
ServiceDependency = Annotated[AlertService, Depends(get_alert_service)]


@router.get("/{elderly_id}", response_model=SuccessResponse[list[AlertRecord]])
async def list_alert_history(
    elderly_id: ElderlyId,
    service: ServiceDependency,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
    severity: Annotated[AlertSeverity | None, Query()] = None,
    alert_status: Annotated[AlertStatus | None, Query(alias="status")] = None,
) -> SuccessResponse[list[AlertRecord]]:
    alerts = await service.list(
        elderly_id,
        limit,
        offset,
        severity,
        alert_status,
    )
    return SuccessResponse(
        message="Alert history retrieved successfully",
        data=alerts,
    )


@router.patch("/{alert_id}", response_model=SuccessResponse[AlertRecord])
async def update_alert_status(
    alert_id: str,
    request: AlertStatusUpdate,
    service: ServiceDependency,
) -> SuccessResponse[AlertRecord]:
    try:
        alert = await service.update_status(alert_id, request.status)
    except AlertNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except AlertConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return SuccessResponse(message="Alert status updated successfully", data=alert)

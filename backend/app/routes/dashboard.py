from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.dependencies import get_dashboard_service
from app.models.common import SuccessResponse
from app.models.dashboard import DashboardSummary
from app.models.elderly import ElderlyId
from app.services.dashboard import DashboardService
from app.services.elderly import ElderlyProfileNotFound
from app.services.auth import Principal, require_relationship_permission


router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])
ServiceDependency = Annotated[DashboardService, Depends(get_dashboard_service)]


@router.get("/{elderly_id}", response_model=SuccessResponse[DashboardSummary])
async def get_dashboard_summary(
    elderly_id: ElderlyId,
    service: ServiceDependency,
    _: Annotated[Principal, require_relationship_permission("read_dashboard")],
) -> SuccessResponse[DashboardSummary]:
    try:
        summary = await service.get_summary(elderly_id)
    except ElderlyProfileNotFound as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    return SuccessResponse(
        message="Dashboard summary retrieved successfully",
        data=summary,
    )

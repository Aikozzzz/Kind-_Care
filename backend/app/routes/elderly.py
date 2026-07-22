from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.dependencies import get_elderly_service
from app.models.common import SuccessResponse
from app.models.elderly import (
    ElderlyId,
    ElderlyProfile,
    ElderlyProfileCreate,
    ElderlyProfileUpdate,
)
from app.services.elderly import (
    ElderlyProfileAlreadyExists,
    ElderlyProfileNotFound,
    ElderlyProfileService,
)


router = APIRouter(prefix="/api/elderly", tags=["elderly"])
ServiceDependency = Annotated[ElderlyProfileService, Depends(get_elderly_service)]


@router.post(
    "",
    response_model=SuccessResponse[ElderlyProfile],
    status_code=status.HTTP_201_CREATED,
)
async def create_elderly_profile(
    profile: ElderlyProfileCreate,
    service: ServiceDependency,
) -> SuccessResponse[ElderlyProfile]:
    try:
        created = await service.create_profile(profile)
    except ElderlyProfileAlreadyExists as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    return SuccessResponse(
        message="Elderly profile created successfully",
        data=created,
    )


@router.get("", response_model=SuccessResponse[list[ElderlyProfile]])
async def list_elderly_profiles(
    service: ServiceDependency,
    include_inactive: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> SuccessResponse[list[ElderlyProfile]]:
    profiles = await service.list_profiles(
        include_inactive=include_inactive,
        limit=limit,
        offset=offset,
    )
    return SuccessResponse(
        message="Elderly profiles retrieved successfully",
        data=profiles,
    )


@router.get("/{elderly_id}", response_model=SuccessResponse[ElderlyProfile])
async def get_elderly_profile(
    elderly_id: ElderlyId,
    service: ServiceDependency,
) -> SuccessResponse[ElderlyProfile]:
    try:
        profile = await service.get_profile(elderly_id)
    except ElderlyProfileNotFound as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    return SuccessResponse(
        message="Elderly profile retrieved successfully",
        data=profile,
    )


@router.patch("/{elderly_id}", response_model=SuccessResponse[ElderlyProfile])
async def update_elderly_profile(
    elderly_id: ElderlyId,
    updates: ElderlyProfileUpdate,
    service: ServiceDependency,
) -> SuccessResponse[ElderlyProfile]:
    try:
        profile = await service.update_profile(elderly_id, updates)
    except ElderlyProfileNotFound as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    return SuccessResponse(
        message="Elderly profile updated successfully",
        data=profile,
    )


@router.delete("/{elderly_id}", response_model=SuccessResponse[ElderlyProfile])
async def delete_elderly_profile(
    elderly_id: ElderlyId,
    service: ServiceDependency,
) -> SuccessResponse[ElderlyProfile]:
    try:
        profile = await service.delete_profile(elderly_id)
    except ElderlyProfileNotFound as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    return SuccessResponse(
        message="Elderly profile deleted successfully",
        data=profile,
    )

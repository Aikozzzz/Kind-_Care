from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pymongo.asynchronous.database import AsyncDatabase

from app.dependencies import get_database
from app.models.common import SuccessResponse
from app.models.relationship import (
    AccessRequestCreate,
    AccessRequestRecord,
    RelationshipRecord,
)
from app.services.auth import Principal, get_current_principal, require_admin_access


router = APIRouter(prefix="/api/access-requests", tags=["access-requests"])
DatabaseDependency = Annotated[AsyncDatabase, Depends(get_database)]
PrincipalDependency = Annotated[Principal, Depends(get_current_principal)]
AdminDependency = Annotated[Principal, Depends(require_admin_access)]


@router.post("", response_model=SuccessResponse[AccessRequestRecord], status_code=201)
async def create_access_request(
    request: AccessRequestCreate,
    database: DatabaseDependency,
    principal: PrincipalDependency,
) -> SuccessResponse[AccessRequestRecord]:
    profile = await database.elderly_profiles.find_one(
        {"elderly_id": request.elderly_id, "active": True}, {"elderly_id": 1}
    )
    if profile is None:
        raise HTTPException(status_code=404, detail="Resident was not found")
    now = datetime.now(UTC)
    document = {
        "request_id": str(uuid4()),
        "account_id": principal.account_id,
        "elderly_id": request.elderly_id,
        "permissions": sorted(set(request.permissions)),
        "status": "pending",
        "created_at": now,
        "reviewed_at": None,
    }
    await database.access_requests.insert_one(document)
    return SuccessResponse(message="Access request submitted", data=AccessRequestRecord(**document))


@router.get("", response_model=SuccessResponse[list[AccessRequestRecord]])
async def list_access_requests(
    database: DatabaseDependency,
    _: AdminDependency,
) -> SuccessResponse[list[AccessRequestRecord]]:
    documents = await database.access_requests.find({"status": "pending"}).sort("created_at", 1).to_list(length=100)
    return SuccessResponse(
        message="Access requests retrieved",
        data=[AccessRequestRecord(**document) for document in documents],
    )


@router.post("/{request_id}/approve", response_model=SuccessResponse[RelationshipRecord])
async def approve_access_request(
    request_id: str,
    database: DatabaseDependency,
    principal: AdminDependency,
) -> SuccessResponse[RelationshipRecord]:
    now = datetime.now(UTC)
    request = await database.access_requests.find_one_and_update(
        {"request_id": request_id, "status": "pending"},
        {"$set": {"status": "approved", "reviewed_at": now}},
    )
    if request is None:
        raise HTTPException(status_code=404, detail="Access request was not found")
    relationship = {
        "relationship_id": str(uuid4()),
        "account_id": request["account_id"],
        "elderly_id": request["elderly_id"],
        "relationship_type": "family",
        "permissions": request["permissions"],
        "status": "active",
        "created_by_account_id": principal.account_id,
        "created_at": now,
        "updated_at": now,
        "revoked_at": None,
    }
    try:
        await database.account_elderly_relationships.insert_one(relationship)
    except Exception as error:
        if getattr(error, "code", None) == 11000:
            raise HTTPException(status_code=409, detail="Relationship already exists") from error
        raise
    return SuccessResponse(message="Access request approved", data=RelationshipRecord(**relationship))

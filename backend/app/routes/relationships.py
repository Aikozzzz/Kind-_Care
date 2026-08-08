from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.dependencies import get_database
from app.models.common import SuccessResponse
from app.models.relationship import (
    AdminRelationshipRecord,
    RelationshipCreate,
    RelationshipRecord,
    RelationshipUpdate,
)
from app.services.auth import Principal, get_current_principal, require_admin_access
from pymongo import ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase


router = APIRouter(prefix="/api/relationships", tags=["relationships"])
DatabaseDependency = Annotated[AsyncDatabase, Depends(get_database)]
AdminDependency = Annotated[Principal, Depends(require_admin_access)]


@router.post("", response_model=SuccessResponse[RelationshipRecord], status_code=201)
async def create_relationship(
    request: RelationshipCreate,
    database: DatabaseDependency,
    principal: AdminDependency,
) -> SuccessResponse[RelationshipRecord]:
    profile = await database.elderly_profiles.find_one(
        {"elderly_id": request.elderly_id, "active": True}, {"elderly_id": 1}
    )
    account = await database.accounts.find_one(
        {"account_id": request.account_id, "status": "active"}, {"account_id": 1}
    )
    if profile is None or account is None:
        raise HTTPException(status_code=404, detail="Account or resident was not found")
    now = datetime.now(UTC)
    document = {
        "relationship_id": str(uuid4()),
        "account_id": request.account_id,
        "elderly_id": request.elderly_id,
        "relationship_type": request.relationship_type,
        "permissions": sorted(set(request.permissions)),
        "status": "active",
        "created_by_account_id": principal.account_id,
        "created_at": now,
        "updated_at": now,
        "revoked_at": None,
    }
    try:
        await database.account_elderly_relationships.insert_one(document)
    except Exception as error:
        if getattr(error, "code", None) == 11000:
            raise HTTPException(status_code=409, detail="Relationship already exists") from error
        raise
    return SuccessResponse(
        message="Relationship created", data=RelationshipRecord(**document)
    )


@router.get("", response_model=SuccessResponse[list[AdminRelationshipRecord]])
async def list_relationships(
    database: DatabaseDependency,
    _: AdminDependency,
    elderly_id: Annotated[str | None, Query(min_length=1, max_length=50)] = None,
) -> SuccessResponse[list[AdminRelationshipRecord]]:
    query: dict[str, object] = {"status": "active"}
    if elderly_id is not None:
        query["elderly_id"] = elderly_id
    documents = await database.account_elderly_relationships.find(query).sort(
        "created_at", -1
    ).to_list(length=100)
    records: list[AdminRelationshipRecord] = []
    for document in documents:
        account = await database.accounts.find_one(
            {"account_id": document["account_id"]},
            {"login_name": 1, "display_name": 1, "role": 1},
        )
        if account is None:
            continue
        records.append(
            AdminRelationshipRecord(
                **document,
                account_login_name=str(account["login_name"]),
                account_display_name=str(account["display_name"]),
                account_role=str(account["role"]),
            )
        )
    return SuccessResponse(message="Relationships retrieved", data=records)


@router.patch("/{relationship_id}", response_model=SuccessResponse[RelationshipRecord])
async def update_relationship(
    relationship_id: str,
    request: RelationshipUpdate,
    database: DatabaseDependency,
    _: AdminDependency,
) -> SuccessResponse[RelationshipRecord]:
    relationship = await database.account_elderly_relationships.find_one_and_update(
        {"relationship_id": relationship_id, "status": "active"},
        {
            "$set": {
                "permissions": sorted(set(request.permissions)),
                "updated_at": datetime.now(UTC),
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    if relationship is None:
        raise HTTPException(status_code=404, detail="Relationship was not found")
    return SuccessResponse(
        message="Relationship updated", data=RelationshipRecord(**relationship)
    )


@router.delete("/{relationship_id}", response_model=SuccessResponse[RelationshipRecord])
async def revoke_relationship(
    relationship_id: str,
    database: DatabaseDependency,
    _: AdminDependency,
) -> SuccessResponse[RelationshipRecord]:
    now = datetime.now(UTC)
    relationship = await database.account_elderly_relationships.find_one_and_update(
        {"relationship_id": relationship_id, "status": "active"},
        {"$set": {"status": "revoked", "revoked_at": now, "updated_at": now}},
        return_document=ReturnDocument.AFTER,
    )
    if relationship is None:
        raise HTTPException(status_code=404, detail="Relationship was not found")
    return SuccessResponse(
        message="Relationship revoked", data=RelationshipRecord(**relationship)
    )


@router.get("/mine", response_model=SuccessResponse[list[RelationshipRecord]])
async def list_my_relationships(
    database: DatabaseDependency,
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> SuccessResponse[list[RelationshipRecord]]:
    documents = await database.account_elderly_relationships.find(
        {"account_id": principal.account_id, "status": "active"}
    ).sort("created_at", -1).to_list(length=100)
    return SuccessResponse(
        message="Relationships retrieved",
        data=[RelationshipRecord(**document) for document in documents],
    )

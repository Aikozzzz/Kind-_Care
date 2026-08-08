from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models.account import AccountRole
from app.models.elderly import ElderlyId


RelationshipType = Literal["self", "staff_assignment", "family"]
RelationshipStatus = Literal["active", "revoked"]
AccessRequestStatus = Literal["pending", "approved", "rejected"]


class RelationshipCreate(BaseModel):
    account_id: str = Field(min_length=1, max_length=80)
    elderly_id: ElderlyId
    relationship_type: RelationshipType
    permissions: list[str] = Field(min_length=1, max_length=20)


class RelationshipRecord(BaseModel):
    relationship_id: str
    account_id: str
    elderly_id: str
    relationship_type: RelationshipType
    permissions: list[str]
    status: RelationshipStatus
    created_by_account_id: str
    created_at: datetime
    updated_at: datetime
    revoked_at: datetime | None = None


class RelationshipUpdate(BaseModel):
    permissions: list[str] = Field(min_length=1, max_length=20)


class AdminRelationshipRecord(RelationshipRecord):
    account_login_name: str
    account_display_name: str
    account_role: AccountRole


class AccessRequestCreate(BaseModel):
    elderly_id: ElderlyId
    permissions: list[str] = Field(
        default=["read_dashboard", "query_telegram_status", "receive_telegram_alerts"],
        min_length=1,
        max_length=20,
    )


class AccessRequestRecord(BaseModel):
    request_id: str
    account_id: str
    elderly_id: str
    permissions: list[str]
    status: AccessRequestStatus
    created_at: datetime
    reviewed_at: datetime | None = None

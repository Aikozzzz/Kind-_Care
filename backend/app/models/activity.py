from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

from app.models.elderly import ElderlyId


class ActivityFields(BaseModel):
    elderly_id: ElderlyId
    value: Literal["active", "inactive"]
    recorded_at: datetime

    @field_validator("recorded_at")
    @classmethod
    def require_aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("recorded_at must include a timezone")
        return value.astimezone(UTC)


class ActivityEventCreate(ActivityFields):
    model_config = ConfigDict(extra="forbid")


class ActivityEvent(ActivityFields):
    event_id: UUID
    received_at: datetime

    @field_validator("received_at")
    @classmethod
    def require_aware_received_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("received_at must include a timezone")
        return value.astimezone(UTC)


class QueuedActivityEvent(BaseModel):
    event_id: UUID
    elderly_id: ElderlyId
    status: Literal["queued"] = "queued"


class ActivityRecord(ActivityEvent):
    created_at: datetime

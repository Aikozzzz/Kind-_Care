from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

from app.models.elderly import ElderlyId


class DeviceHeartbeatFields(BaseModel):
    elderly_id: ElderlyId
    recorded_at: datetime

    @field_validator("recorded_at")
    @classmethod
    def require_aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("recorded_at must include a timezone")
        return value.astimezone(UTC)


class DeviceHeartbeatCreate(DeviceHeartbeatFields):
    model_config = ConfigDict(extra="forbid")


class DeviceEvent(DeviceHeartbeatFields):
    event_id: UUID
    received_at: datetime

    @field_validator("received_at")
    @classmethod
    def require_aware_received_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("received_at must include a timezone")
        return value.astimezone(UTC)


class QueuedDeviceEvent(BaseModel):
    event_id: UUID
    elderly_id: ElderlyId
    status: Literal["queued"] = "queued"


class DeviceEventRecord(DeviceEvent):
    created_at: datetime


class DeviceStatusRecord(BaseModel):
    elderly_id: ElderlyId
    event_id: UUID
    status: Literal["online", "offline"]
    last_seen: datetime
    updated_at: datetime

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.elderly import ElderlyId


ReminderStatus = Literal["pending", "missed", "taken"]


class ReminderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    elderly_id: ElderlyId
    medicine_name: str = Field(min_length=1, max_length=200)
    scheduled_for: datetime
    instructions: str | None = Field(default=None, max_length=1000)

    @field_validator("medicine_name")
    @classmethod
    def medicine_name_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("medicine_name must not be blank")
        return value

    @field_validator("scheduled_for")
    @classmethod
    def scheduled_for_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("scheduled_for must include a timezone")
        return value.astimezone(UTC)


class ReminderStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    elderly_id: ElderlyId
    status: Literal["taken"]


class ReminderRecord(BaseModel):
    reminder_id: UUID
    elderly_id: ElderlyId
    medicine_name: str
    scheduled_for: datetime
    instructions: str | None = None
    status: ReminderStatus
    created_at: datetime
    updated_at: datetime
    taken_at: datetime | None = None

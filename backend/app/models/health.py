from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from app.models.elderly import ElderlyId


MovementStatus = Literal["active", "inactive"]
MedicineStatus = Literal["taken", "missed", "not_due"]
RiskLevel = Literal["normal", "warning", "emergency"]
AlertSeverity = Literal["warning", "emergency"]
AlertStatus = Literal["unresolved", "acknowledged", "resolved"]
AlertLifecycleTarget = Literal["acknowledged", "resolved"]
IdempotencyKey = Annotated[
    str,
    StringConstraints(min_length=1, max_length=128, pattern=r"^[!-~]+$"),
]


def utc_now() -> datetime:
    return datetime.now(UTC)


class HealthFields(BaseModel):
    elderly_id: ElderlyId
    heart_rate: int = Field(ge=20, le=250)
    temperature: float = Field(ge=25, le=45)
    oxygen_level: int = Field(ge=50, le=100)
    blood_pressure: str | None = None
    movement_status: MovementStatus
    medicine_status: MedicineStatus
    emergency_pressed: bool = False
    recorded_at: datetime = Field(default_factory=utc_now)

    @field_validator("blood_pressure")
    @classmethod
    def validate_blood_pressure(cls, value: str | None) -> str | None:
        if value is None:
            return value
        parts = value.split("/")
        if len(parts) != 2 or not all(part.isdigit() for part in parts):
            raise ValueError("blood_pressure must use systolic/diastolic format")
        systolic, diastolic = (int(part) for part in parts)
        if not 60 <= systolic <= 250 or not 30 <= diastolic <= 150:
            raise ValueError("blood_pressure is outside the supported range")
        if systolic <= diastolic:
            raise ValueError("systolic pressure must exceed diastolic pressure")
        return value

    @field_validator("recorded_at")
    @classmethod
    def recorded_at_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("recorded_at must include a timezone")
        return value.astimezone(UTC)


class HealthEventCreate(HealthFields):
    model_config = ConfigDict(extra="forbid")


class HealthEvent(HealthFields):
    event_id: UUID = Field(default_factory=uuid4)


class QueuedHealthEvent(BaseModel):
    event_id: UUID
    elderly_id: ElderlyId
    status: Literal["queued"] = "queued"


class HealthRecord(HealthEvent):
    risk_level: RiskLevel
    created_at: datetime


class AlertRecord(BaseModel):
    alert_id: str
    event_id: UUID
    elderly_id: ElderlyId
    alert_type: str
    severity: AlertSeverity
    status: AlertStatus
    message: str
    created_at: datetime
    updated_at: datetime | None = None
    acknowledged_at: datetime | None = None
    resolved_at: datetime | None = None

    @field_validator("alert_id")
    @classmethod
    def validate_alert_id(cls, value: str) -> str:
        if str(UUID(value)) != value:
            raise ValueError("alert_id must be a canonical UUID string")
        return value


class AlertStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: AlertLifecycleTarget

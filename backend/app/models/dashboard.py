from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.models.elderly import ElderlyProfile
from app.models.health import AlertRecord, HealthRecord, RiskLevel
from app.models.activity import ActivityRecord
from app.models.device import DeviceStatusRecord
from app.models.reminder import ReminderRecord


class DashboardSummary(BaseModel):
    profile: ElderlyProfile
    latest_health: HealthRecord | None
    current_risk: RiskLevel
    current_alert: AlertRecord | None
    recent_alerts: list[AlertRecord]
    latest_activity: ActivityRecord | None = None
    device_status: DeviceStatusRecord | None = None
    upcoming_reminders: list[ReminderRecord] = []
    recent_reminders: list[ReminderRecord] = []


class DashboardSummaryMessage(BaseModel):
    type: Literal["summary"] = "summary"
    data: DashboardSummary


class DashboardHeartbeatData(BaseModel):
    sent_at: datetime
    interval_seconds: float
    last_summary_check_at: datetime
    poll_interval_seconds: float


class DashboardHeartbeatMessage(BaseModel):
    type: Literal["heartbeat"] = "heartbeat"
    data: DashboardHeartbeatData


class DashboardErrorData(BaseModel):
    message: str


class DashboardErrorMessage(BaseModel):
    type: Literal["error"] = "error"
    data: DashboardErrorData

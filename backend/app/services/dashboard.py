from datetime import UTC, datetime
from typing import Callable

from pymongo.asynchronous.collection import AsyncCollection
from pymongo.errors import PyMongoError

from app.models.dashboard import DashboardSummary
from app.models.elderly import ElderlyProfile
from app.models.health import AlertRecord, HealthRecord, RiskLevel
from app.models.activity import ActivityRecord
from app.models.device import DeviceStatusRecord
from app.services.elderly import ElderlyProfileNotFound
from app.services.telemetry import bounded_received_at_documents
from app.models.reminder import ReminderRecord


CURRENT_ALERT_SORT = [
    ("created_at", -1),
    ("event_id", -1),
    ("alert_type", 1),
]


class DashboardStorageUnavailable(Exception):
    """Raised when MongoDB cannot build a dashboard summary."""


class DashboardService:
    def __init__(
        self,
        elderly_profiles: AsyncCollection,
        health_logs: AsyncCollection,
        alerts: AsyncCollection,
        recent_alert_limit: int = 10,
        activity_logs: AsyncCollection | None = None,
        device_status: AsyncCollection | None = None,
        reminders: AsyncCollection | None = None,
        upcoming_reminder_limit: int = 10,
        recent_reminder_limit: int = 10,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.elderly_profiles = elderly_profiles
        self.health_logs = health_logs
        self.alerts = alerts
        self.recent_alert_limit = recent_alert_limit
        self.activity_logs = activity_logs
        self.device_status = device_status
        self.reminders = reminders
        self.upcoming_reminder_limit = upcoming_reminder_limit
        self.recent_reminder_limit = recent_reminder_limit
        self.clock = clock

    async def get_summary(self, elderly_id: str) -> DashboardSummary:
        try:
            profile_document = await self.elderly_profiles.find_one(
                {"elderly_id": elderly_id, "active": True}
            )
            if profile_document is None:
                raise ElderlyProfileNotFound(elderly_id)

            health_document = await self.health_logs.find_one(
                {"elderly_id": elderly_id},
                sort=[("recorded_at", -1), ("event_id", -1)],
            )
            alert_cursor = (
                self.alerts.find({"elderly_id": elderly_id})
                .sort(
                    [
                        ("created_at", -1),
                        ("event_id", -1),
                        ("alert_type", 1),
                    ]
                )
                .limit(self.recent_alert_limit)
            )
            alert_documents = await alert_cursor.to_list(
                length=self.recent_alert_limit
            )
            current_alert_document = None
            unresolved_risk: RiskLevel = "normal"
            for severity in ("emergency", "warning"):
                for alert_status in ("unresolved", "acknowledged"):
                    current_alert_document = await self.alerts.find_one(
                        {
                            "elderly_id": elderly_id,
                            "status": alert_status,
                            "severity": severity,
                        },
                        sort=CURRENT_ALERT_SORT,
                        hint="alert_current_risk",
                    )
                    if current_alert_document is not None:
                        break
                if current_alert_document is not None:
                    unresolved_risk = severity
                    break
            if self.activity_logs is not None:
                activity_documents = await bounded_received_at_documents(
                    self.activity_logs,
                    elderly_id,
                    1,
                    normal_index="activity_history_latest",
                    legacy_created_index="activity_history_legacy",
                    legacy_recorded_index="activity_history_legacy_recorded",
                )
                activity_document = activity_documents[0] if activity_documents else None
            else:
                activity_document = None
            device_document = (
                await self.device_status.find_one(
                    {"elderly_id": elderly_id},
                    sort=[("last_seen", -1), ("event_id", -1)],
                )
                if self.device_status is not None
                else None
            )
            if self.reminders is not None:
                now = self.clock()
                upcoming_cursor = (
                    self.reminders.find(
                        {
                            "elderly_id": elderly_id,
                            "status": "pending",
                            "scheduled_for": {"$gte": now},
                        }
                    )
                    .sort([("scheduled_for", 1), ("reminder_id", 1)])
                    .limit(self.upcoming_reminder_limit)
                )
                recent_cursor = (
                    self.reminders.find(
                        {
                            "elderly_id": elderly_id,
                            "$or": [
                                {"scheduled_for": {"$lt": now}},
                                {"status": {"$in": ["missed", "taken"]}},
                            ],
                        }
                    )
                    .sort([("scheduled_for", -1), ("reminder_id", -1)])
                    .limit(self.recent_reminder_limit)
                )
                upcoming_documents = await upcoming_cursor.to_list(
                    length=self.upcoming_reminder_limit
                )
                recent_reminder_documents = await recent_cursor.to_list(
                    length=self.recent_reminder_limit
                )
            else:
                upcoming_documents = []
                recent_reminder_documents = []
        except PyMongoError as error:
            raise DashboardStorageUnavailable() from error

        profile = ElderlyProfile.model_validate(profile_document)
        latest_health = (
            HealthRecord.model_validate(health_document)
            if health_document is not None
            else None
        )
        recent_alerts = [
            AlertRecord.model_validate(document) for document in alert_documents
        ]
        selected_alert = (
            AlertRecord.model_validate(current_alert_document)
            if current_alert_document is not None
            else None
        )
        current_risk = _current_risk(latest_health, unresolved_risk)
        current_alert = (
            selected_alert
            if selected_alert is not None and selected_alert.severity == current_risk
            else None
        )
        latest_activity = (
            ActivityRecord.model_validate(activity_document)
            if activity_document is not None
            else None
        )
        latest_device = (
            DeviceStatusRecord.model_validate(device_document)
            if device_document is not None
            else None
        )
        upcoming_reminders = [
            ReminderRecord.model_validate(document) for document in upcoming_documents
        ]
        recent_reminders = [
            ReminderRecord.model_validate(document)
            for document in recent_reminder_documents
        ]
        return DashboardSummary(
            profile=profile,
            latest_health=latest_health,
            current_risk=current_risk,
            current_alert=current_alert,
            recent_alerts=recent_alerts,
            latest_activity=latest_activity,
            device_status=latest_device,
            upcoming_reminders=upcoming_reminders,
            recent_reminders=recent_reminders,
        )


def _current_risk(
    latest_health: HealthRecord | None,
    unresolved_risk: RiskLevel,
) -> RiskLevel:
    rank: dict[RiskLevel, int] = {"normal": 0, "warning": 1, "emergency": 2}
    levels: list[RiskLevel] = [
        latest_health.risk_level if latest_health is not None else "normal",
        unresolved_risk,
    ]
    return max(levels, key=rank.__getitem__)

from datetime import UTC, datetime
from typing import Callable

from pymongo import ReturnDocument
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.errors import PyMongoError

from app.models.health import AlertRecord


class AlertNotFound(Exception):
    def __init__(self, alert_id: str) -> None:
        super().__init__(f"Alert '{alert_id}' was not found")


class AlertConflict(Exception):
    pass


class AlertStorageUnavailable(Exception):
    pass


class AlertService:
    def __init__(
        self,
        alerts: AsyncCollection,
        activity_state: AsyncCollection | None = None,
        device_status: AsyncCollection | None = None,
        reminders: AsyncCollection | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.alerts = alerts
        self.activity_state = activity_state
        self.device_status = device_status
        self.reminders = reminders
        self.clock = clock

    async def list(self, elderly_id, limit, offset, severity, alert_status):
        query: dict[str, object] = {"elderly_id": elderly_id}
        if severity is not None:
            query["severity"] = severity
        if alert_status is not None:
            query["status"] = alert_status
        try:
            cursor = (
                self.alerts.find(query)
                .sort([("created_at", -1), ("event_id", -1), ("alert_type", 1)])
                .skip(offset)
                .limit(limit)
            )
            documents = await cursor.to_list(length=limit)
        except PyMongoError as error:
            raise AlertStorageUnavailable() from error
        return [AlertRecord.model_validate(document) for document in documents]

    async def update_status(self, alert_id: str, target_status: str) -> AlertRecord:
        now = self.clock()

        async def callback(session: object) -> dict[str, object]:
            current = await self.alerts.find_one({"alert_id": alert_id}, session=session)
            if current is None:
                raise AlertNotFound(alert_id)
            current_status = current["status"]
            if current_status == target_status:
                return current
            if target_status == "resolved":
                await self._reject_active_source(current, session)
            allowed = current_status == "unresolved" or (
                current_status == "acknowledged" and target_status == "resolved"
            )
            if not allowed:
                raise AlertConflict(
                    f"Alert cannot transition from {current_status} to {target_status}"
                )
            timestamp_field = (
                "acknowledged_at" if target_status == "acknowledged" else "resolved_at"
            )
            updated = await self.alerts.find_one_and_update(
                {"alert_id": alert_id, "status": current_status},
                {
                    "$set": {
                        "status": target_status,
                        timestamp_field: now,
                        "updated_at": now,
                    }
                },
                return_document=ReturnDocument.AFTER,
                session=session,
            )
            if updated is None:
                raise AlertConflict("Alert status changed concurrently")
            return updated

        try:
            async with self.alerts.database.client.start_session() as session:
                document = await session.with_transaction(callback)
        except (AlertNotFound, AlertConflict):
            raise
        except PyMongoError as error:
            raise AlertStorageUnavailable() from error
        return AlertRecord.model_validate(document)

    async def _reject_active_source(
        self, alert: dict[str, object], session: object
    ) -> None:
        if alert.get("alert_type") == "long_inactivity" and self.activity_state is not None:
            active = await self.activity_state.find_one(
                {
                    "elderly_id": alert["elderly_id"],
                    "value": "inactive",
                    "episode_id": alert.get("episode_id"),
                },
                {"_id": 1},
                session=session,
            )
        elif alert.get("alert_type") == "device_offline" and self.device_status is not None:
            active = await self.device_status.find_one(
                {
                    "elderly_id": alert["elderly_id"],
                    "status": "offline",
                    "offline_episode_id": alert.get("episode_id"),
                },
                {"_id": 1},
                session=session,
            )
        elif alert.get("alert_type") == "missed_reminder" and self.reminders is not None:
            episode_id = str(alert.get("episode_id", ""))
            reminder_id = episode_id.removeprefix("reminder:")
            active = None
            if reminder_id != episode_id and reminder_id:
                active = await self.reminders.find_one(
                    {
                        "reminder_id": reminder_id,
                        "elderly_id": alert["elderly_id"],
                        "status": "missed",
                    },
                    {"_id": 1},
                    session=session,
                )
        else:
            active = None
        if active is not None:
            raise AlertConflict("Alert cannot be resolved while its source condition is still active")

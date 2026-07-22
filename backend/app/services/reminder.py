import hashlib
import json
from datetime import UTC, datetime
from typing import Callable
from uuid import UUID, uuid5

from pymongo import ReturnDocument
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.errors import PyMongoError

from app.models.reminder import ReminderCreate, ReminderRecord
from app.services.elderly import ElderlyProfileNotFound
from app.services.health import hash_idempotency_key


KINDCARE_REMINDER_NAMESPACE = UUID("38eea589-ef65-53a8-891c-3a438b555261")


class ReminderNotFound(Exception):
    def __init__(self, reminder_id: str) -> None:
        super().__init__(f"Reminder '{reminder_id}' was not found")


class ReminderConflict(Exception):
    pass


class ReminderStorageUnavailable(Exception):
    pass


def derive_reminder_id(elderly_id: str, idempotency_key: str) -> UUID:
    return uuid5(KINDCARE_REMINDER_NAMESPACE, f"{elderly_id}\0{idempotency_key}")


def _payload_hash(request: ReminderCreate) -> str:
    payload = request.model_dump(mode="json")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class ReminderService:
    def __init__(
        self,
        elderly_profiles: AsyncCollection,
        reminder_idempotency: AsyncCollection,
        reminders: AsyncCollection,
        alerts: AsyncCollection,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.elderly_profiles = elderly_profiles
        self.reminder_idempotency = reminder_idempotency
        self.reminders = reminders
        self.alerts = alerts
        self.clock = clock

    async def create(
        self, request: ReminderCreate, idempotency_key: str
    ) -> ReminderRecord:
        reminder_id = str(derive_reminder_id(request.elderly_id, idempotency_key))
        key_hash = hash_idempotency_key(idempotency_key)
        payload_hash = _payload_hash(request)
        now = self.clock()
        async def callback(session: object) -> dict[str, object]:
            reservation = await self.reminder_idempotency.find_one(
                {"elderly_id": request.elderly_id, "key_hash": key_hash},
                session=session,
            )
            if reservation is not None and reservation.get("payload_hash") != payload_hash:
                raise ReminderConflict(
                    "Idempotency-Key was already used with a different reminder"
                )
            if reservation is None:
                profile = await self.elderly_profiles.find_one(
                    {"elderly_id": request.elderly_id, "active": True},
                    {"_id": 1},
                    session=session,
                )
                if profile is None:
                    raise ElderlyProfileNotFound(request.elderly_id)
                reservation = await self.reminder_idempotency.find_one_and_update(
                    {"elderly_id": request.elderly_id, "key_hash": key_hash},
                    {
                        "$setOnInsert": {
                            "elderly_id": request.elderly_id,
                            "key_hash": key_hash,
                            "payload_hash": payload_hash,
                            "reminder_id": reminder_id,
                            "created_at": now,
                        }
                    },
                    upsert=True,
                    return_document=ReturnDocument.AFTER,
                    session=session,
                )
            document = {
                "reminder_id": reservation["reminder_id"],
                **request.model_dump(),
                "status": "pending",
                "created_at": reservation["created_at"],
                "updated_at": reservation["created_at"],
            }
            await self.reminders.update_one(
                {"reminder_id": reservation["reminder_id"]},
                {"$setOnInsert": document},
                upsert=True,
                session=session,
            )
            stored = await self.reminders.find_one(
                {"reminder_id": reservation["reminder_id"]},
                session=session,
            )
            if stored is None:
                raise ReminderStorageUnavailable()
            return stored

        try:
            async with self.reminders.database.client.start_session() as session:
                stored = await session.with_transaction(callback)
        except (ElderlyProfileNotFound, ReminderConflict, ReminderStorageUnavailable):
            raise
        except PyMongoError as error:
            raise ReminderStorageUnavailable() from error
        return ReminderRecord.model_validate(stored)

    async def list(
        self,
        elderly_id: str,
        limit: int,
        offset: int,
        reminder_status: str | None,
    ) -> list[ReminderRecord]:
        query: dict[str, object] = {"elderly_id": elderly_id}
        if reminder_status is not None:
            query["status"] = reminder_status
        try:
            cursor = (
                self.reminders.find(query)
                .sort([("scheduled_for", -1), ("reminder_id", -1)])
                .skip(offset)
                .limit(limit)
            )
            documents = await cursor.to_list(length=limit)
        except PyMongoError as error:
            raise ReminderStorageUnavailable() from error
        return [ReminderRecord.model_validate(document) for document in documents]

    async def mark_taken(self, reminder_id: str, elderly_id: str) -> ReminderRecord:
        now = self.clock()

        async def callback(session: object) -> dict[str, object]:
            reminder = await self.reminders.find_one(
                {"reminder_id": reminder_id, "elderly_id": elderly_id},
                session=session,
            )
            if reminder is None:
                raise ReminderNotFound(reminder_id)
            if reminder.get("status") not in {"pending", "missed", "taken"}:
                raise ReminderConflict("Reminder status cannot transition to taken")
            if reminder["status"] != "taken":
                reminder = await self.reminders.find_one_and_update(
                    {
                        "reminder_id": reminder_id,
                        "elderly_id": elderly_id,
                        "status": {"$in": ["pending", "missed"]},
                    },
                    {
                        "$set": {
                            "status": "taken",
                            "taken_at": now,
                            "updated_at": now,
                        }
                    },
                    return_document=ReturnDocument.AFTER,
                    session=session,
                )
                if reminder is None:
                    raise ReminderConflict("Reminder status changed concurrently")
            await self.alerts.update_many(
                {
                    "alert_type": "missed_reminder",
                    "elderly_id": elderly_id,
                    "episode_id": f"reminder:{reminder_id}",
                    "status": {"$in": ["unresolved", "acknowledged"]},
                },
                {
                    "$set": {
                        "status": "resolved",
                        "resolved_at": now,
                        "updated_at": now,
                    }
                },
                session=session,
            )
            return reminder

        try:
            async with self.reminders.database.client.start_session() as session:
                document = await session.with_transaction(callback)
        except (ReminderNotFound, ReminderConflict):
            raise
        except PyMongoError as error:
            raise ReminderStorageUnavailable() from error
        return ReminderRecord.model_validate(document)

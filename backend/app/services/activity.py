from datetime import UTC, datetime
from typing import Callable, Protocol
from uuid import UUID, uuid5

from pymongo import ReturnDocument
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.errors import PyMongoError

from app.celery_app import ActivityBrokerUnavailable, CeleryActivityDispatcher
from app.models.activity import ActivityEvent, ActivityEventCreate, ActivityRecord
from app.services.elderly import ElderlyProfileNotFound
from app.services.health import hash_idempotency_key
from app.services.idempotency import backfill_legacy_reservation, canonical_payload_hash, run_reservation_transaction, session_options, verify_payload_hash
from app.services.telemetry import bounded_received_at_documents


KINDCARE_ACTIVITY_EVENT_NAMESPACE = UUID("f9d3f20b-70c5-55e3-a449-335637ab8d45")


def derive_activity_event_id(elderly_id: str, idempotency_key: str) -> UUID:
    return uuid5(KINDCARE_ACTIVITY_EVENT_NAMESPACE, f"{elderly_id}\0{idempotency_key}")


class ActivityStorageUnavailable(Exception):
    """Raised when activity storage cannot complete an operation."""


class ActivityDispatcher(Protocol):
    def dispatch(self, event: ActivityEvent) -> None: ...


class ActivityEventService:
    def __init__(self, elderly_profiles: AsyncCollection, activity_idempotency: AsyncCollection, activity_logs: AsyncCollection, dispatcher: ActivityDispatcher, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self.elderly_profiles = elderly_profiles
        self.activity_idempotency = activity_idempotency
        self.activity_logs = activity_logs
        self.dispatcher = dispatcher
        self.clock = clock

    async def queue_event(self, request: ActivityEventCreate, event_id: UUID, idempotency_key: str) -> ActivityEvent:
        received_at = self.clock()
        payload_hash = canonical_payload_hash(request.model_dump(mode="json"))
        key_hash = hash_idempotency_key(idempotency_key)

        async def callback(session: object | None) -> dict[str, object]:
            options = session_options(session)
            identity = {"elderly_id": request.elderly_id, "key_hash": key_hash}
            finder = getattr(self.activity_idempotency, "find_one", None)
            existing = (
                await finder(identity, **options) if finder is not None else None
            )
            if existing is not None:
                existing = await backfill_legacy_reservation(
                    existing,
                    self.activity_idempotency,
                    self.activity_logs,
                    lambda event: canonical_payload_hash(
                        ActivityEventCreate.model_validate(
                            {
                                field: event[field]
                                for field in ActivityEventCreate.model_fields
                                if field in event
                            }
                        ).model_dump(mode="json")
                    ),
                    session,
                )
                verify_payload_hash(existing, payload_hash)
                return existing
            profile = await self.elderly_profiles.find_one(
                {"elderly_id": request.elderly_id, "active": True}, {"_id": 1}, **options
            )
            if profile is None:
                raise ElderlyProfileNotFound(request.elderly_id)
            reservation = await self.activity_idempotency.find_one_and_update(
                identity,
                {"$setOnInsert": {"elderly_id": request.elderly_id, "key_hash": key_hash, "payload_hash": payload_hash, "event_id": str(event_id), "received_at": received_at, "created_at": received_at}},
                upsert=True,
                return_document=ReturnDocument.AFTER,
                **options,
            )
            reservation = await backfill_legacy_reservation(
                reservation,
                self.activity_idempotency,
                self.activity_logs,
                lambda event: canonical_payload_hash(
                    ActivityEventCreate.model_validate(
                        {
                            field: event[field]
                            for field in ActivityEventCreate.model_fields
                            if field in event
                        }
                    ).model_dump(mode="json")
                ),
                session,
            )
            verify_payload_hash(reservation, payload_hash)
            return reservation

        try:
            reservation = await run_reservation_transaction(
                self.activity_idempotency, callback
            )
        except PyMongoError as error:
            raise ActivityStorageUnavailable() from error
        event = ActivityEvent(event_id=event_id, received_at=reservation["received_at"], **request.model_dump())
        self.dispatcher.dispatch(event)
        return event

    async def list_activity(self, elderly_id: str, limit: int, offset: int) -> list[ActivityRecord]:
        try:
            documents = await bounded_received_at_documents(
                self.activity_logs,
                elderly_id,
                limit,
                offset,
                "activity_history_latest",
                "activity_history_legacy",
                "activity_history_legacy_recorded",
            )
        except PyMongoError as error:
            raise ActivityStorageUnavailable() from error
        return [ActivityRecord.model_validate(document) for document in documents]


__all__ = ["ActivityBrokerUnavailable", "ActivityEventService", "ActivityStorageUnavailable"]

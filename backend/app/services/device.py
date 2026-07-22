from datetime import UTC, datetime
from typing import Callable, Protocol
from uuid import UUID, uuid5

from pymongo import ReturnDocument
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.errors import PyMongoError

from app.celery_app import CeleryDeviceDispatcher, DeviceBrokerUnavailable
from app.models.device import DeviceEvent, DeviceEventRecord, DeviceHeartbeatCreate
from app.services.elderly import ElderlyProfileNotFound
from app.services.health import hash_idempotency_key
from app.services.idempotency import backfill_legacy_reservation, canonical_payload_hash, run_reservation_transaction, session_options, verify_payload_hash
from app.services.telemetry import bounded_received_at_documents


KINDCARE_DEVICE_EVENT_NAMESPACE = UUID("e60ca246-c951-54a2-a513-b052236d617f")


def derive_device_event_id(elderly_id: str, idempotency_key: str) -> UUID:
    return uuid5(KINDCARE_DEVICE_EVENT_NAMESPACE, f"{elderly_id}\0{idempotency_key}")


class DeviceStorageUnavailable(Exception):
    """Raised when device storage cannot complete an operation."""


class DeviceDispatcher(Protocol):
    def dispatch(self, event: DeviceEvent) -> None: ...


class DeviceEventService:
    def __init__(self, elderly_profiles: AsyncCollection, device_idempotency: AsyncCollection, device_events: AsyncCollection, dispatcher: DeviceDispatcher, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self.elderly_profiles = elderly_profiles
        self.device_idempotency = device_idempotency
        self.device_events = device_events
        self.dispatcher = dispatcher
        self.clock = clock

    async def queue_event(self, request: DeviceHeartbeatCreate, event_id: UUID, idempotency_key: str) -> DeviceEvent:
        received_at = self.clock()
        payload_hash = canonical_payload_hash(request.model_dump(mode="json"))
        key_hash = hash_idempotency_key(idempotency_key)

        async def callback(session: object | None) -> dict[str, object]:
            options = session_options(session)
            identity = {"elderly_id": request.elderly_id, "key_hash": key_hash}
            finder = getattr(self.device_idempotency, "find_one", None)
            existing = (
                await finder(identity, **options) if finder is not None else None
            )
            if existing is not None:
                existing = await backfill_legacy_reservation(
                    existing,
                    self.device_idempotency,
                    self.device_events,
                    lambda event: canonical_payload_hash(
                        DeviceHeartbeatCreate.model_validate(
                            {
                                field: event[field]
                                for field in DeviceHeartbeatCreate.model_fields
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
            reservation = await self.device_idempotency.find_one_and_update(
                identity,
                {"$setOnInsert": {"elderly_id": request.elderly_id, "key_hash": key_hash, "payload_hash": payload_hash, "event_id": str(event_id), "received_at": received_at, "created_at": received_at}},
                upsert=True,
                return_document=ReturnDocument.AFTER,
                **options,
            )
            reservation = await backfill_legacy_reservation(
                reservation,
                self.device_idempotency,
                self.device_events,
                lambda event: canonical_payload_hash(
                    DeviceHeartbeatCreate.model_validate(
                        {
                            field: event[field]
                            for field in DeviceHeartbeatCreate.model_fields
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
                self.device_idempotency, callback
            )
        except PyMongoError as error:
            raise DeviceStorageUnavailable() from error
        event = DeviceEvent(event_id=event_id, received_at=reservation["received_at"], **request.model_dump())
        self.dispatcher.dispatch(event)
        return event

    async def list_events(self, elderly_id: str, limit: int, offset: int) -> list[DeviceEventRecord]:
        try:
            documents = await bounded_received_at_documents(
                self.device_events,
                elderly_id,
                limit,
                offset,
                "device_history_latest",
                "device_history_legacy",
                "device_history_legacy_recorded",
            )
        except PyMongoError as error:
            raise DeviceStorageUnavailable() from error
        return [DeviceEventRecord.model_validate(document) for document in documents]


__all__ = ["DeviceBrokerUnavailable", "DeviceEventService", "DeviceStorageUnavailable"]

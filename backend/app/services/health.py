import hashlib
from typing import Protocol
from uuid import UUID, uuid5

from pymongo import ReturnDocument
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.errors import PyMongoError

from app.celery_app import CeleryHealthDispatcher, HealthBrokerUnavailable
from app.models.health import AlertRecord, HealthEvent, HealthEventCreate, HealthRecord
from app.services.elderly import ElderlyProfileNotFound
from app.services.idempotency import (
    backfill_legacy_reservation,
    canonical_payload_hash,
    run_reservation_transaction,
    session_options,
    verify_payload_hash,
)


class HealthStorageUnavailable(Exception):
    """Raised when MongoDB cannot complete a health-data operation."""


KINDCARE_HEALTH_EVENT_NAMESPACE = UUID("f7e0585d-bcde-56bc-9f9d-bb54bc25abaf")


def derive_health_event_id(elderly_id: str, idempotency_key: str) -> UUID:
    return uuid5(
        KINDCARE_HEALTH_EVENT_NAMESPACE,
        f"{elderly_id}\0{idempotency_key}",
    )


def hash_idempotency_key(idempotency_key: str) -> str:
    return hashlib.sha256(idempotency_key.encode("ascii")).hexdigest()


def health_payload_hash(
    request: HealthEventCreate, recorded_at_provided: bool
) -> str:
    payload = request.model_dump(mode="json", exclude={"recorded_at"})
    payload["recorded_at_provided"] = recorded_at_provided
    if recorded_at_provided:
        payload["recorded_at"] = request.recorded_at.isoformat().replace("+00:00", "Z")
    return canonical_payload_hash(payload)


class HealthDispatcher(Protocol):
    def dispatch(self, event: HealthEvent) -> None: ...


class HealthEventService:
    def __init__(
        self,
        elderly_profiles: AsyncCollection,
        health_idempotency: AsyncCollection,
        health_logs: AsyncCollection,
        alerts: AsyncCollection,
        dispatcher: HealthDispatcher,
    ) -> None:
        self.elderly_profiles = elderly_profiles
        self.health_idempotency = health_idempotency
        self.health_logs = health_logs
        self.alerts = alerts
        self.dispatcher = dispatcher

    async def queue_event(
        self,
        request: HealthEventCreate,
        event_id: UUID,
        idempotency_key: str,
    ) -> HealthEvent:
        recorded_at_provided = "recorded_at" in request.model_fields_set
        payload_hash = health_payload_hash(request, recorded_at_provided)
        key_hash = hash_idempotency_key(idempotency_key)

        async def callback(session: object | None) -> dict[str, object]:
            options = session_options(session)
            identity = {"elderly_id": request.elderly_id, "key_hash": key_hash}
            finder = getattr(self.health_idempotency, "find_one", None)
            existing = (
                await finder(identity, **options) if finder is not None else None
            )
            if existing is not None:
                existing = await backfill_legacy_reservation(
                    existing,
                    self.health_idempotency,
                    self.health_logs,
                    lambda event: health_payload_hash(
                        HealthEventCreate.model_validate(
                            {
                                field: event[field]
                                for field in HealthEventCreate.model_fields
                                if field in event
                            }
                        ),
                        False,
                    ),
                    session,
                )
                verify_payload_hash(existing, payload_hash)
                return existing
            profile = await self.elderly_profiles.find_one(
                {"elderly_id": request.elderly_id, "active": True},
                {"_id": 1},
                **options,
            )
            if profile is None:
                raise ElderlyProfileNotFound(request.elderly_id)
            reservation = await self.health_idempotency.find_one_and_update(
                identity,
                {
                    "$setOnInsert": {
                        "elderly_id": request.elderly_id,
                        "key_hash": key_hash,
                        "payload_hash": payload_hash,
                        "event_id": str(event_id),
                        "recorded_at": request.recorded_at,
                        "created_at": request.recorded_at,
                    }
                },
                upsert=True,
                return_document=ReturnDocument.AFTER,
                **options,
            )
            reservation = await backfill_legacy_reservation(
                reservation,
                self.health_idempotency,
                self.health_logs,
                lambda event: health_payload_hash(
                    HealthEventCreate.model_validate(
                        {
                            field: event[field]
                            for field in HealthEventCreate.model_fields
                            if field in event
                        }
                    ),
                    False,
                ),
                session,
            )
            verify_payload_hash(reservation, payload_hash)
            return reservation

        try:
            reservation = await run_reservation_transaction(
                self.health_idempotency, callback
            )
        except PyMongoError as error:
            raise HealthStorageUnavailable() from error

        event_data = request.model_dump()
        event_data["recorded_at"] = reservation["recorded_at"]
        event = HealthEvent(event_id=event_id, **event_data)
        self.dispatcher.dispatch(event)
        return event

    async def list_health(
        self,
        elderly_id: str,
        limit: int,
        offset: int,
    ) -> list[HealthRecord]:
        try:
            cursor = (
                self.health_logs.find({"elderly_id": elderly_id})
                .sort([("recorded_at", -1), ("event_id", -1)])
                .skip(offset)
                .limit(limit)
            )
            documents = await cursor.to_list(length=limit)
        except PyMongoError as error:
            raise HealthStorageUnavailable() from error
        return [HealthRecord.model_validate(document) for document in documents]

    async def list_alerts(
        self,
        elderly_id: str,
        limit: int,
        offset: int,
        severity: str | None,
        alert_status: str | None,
    ) -> list[AlertRecord]:
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
            raise HealthStorageUnavailable() from error
        return [AlertRecord.model_validate(document) for document in documents]


__all__ = [
    "CeleryHealthDispatcher",
    "HealthBrokerUnavailable",
    "HealthEventService",
    "HealthStorageUnavailable",
]

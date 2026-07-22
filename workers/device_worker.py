import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from typing import Callable

from pymongo.database import Database

from workers.celery_app import celery_app
from workers.activity_worker import (
    ScanLeaseLost,
    acquire_scan_lease,
    release_scan_lease,
    renew_scan_lease,
)
from workers.database import get_database
from workers.health_worker import (
    EventPayloadConflict,
    TRANSIENT_DB_ERRORS,
    derive_public_alert_id,
    parse_recorded_at,
)


def canonicalize_device_event(event: dict[str, object]) -> dict[str, object]:
    return {
        "event_id": str(event["event_id"]),
        "elderly_id": str(event["elderly_id"]),
        "recorded_at": parse_recorded_at(event["recorded_at"]),
        "received_at": parse_recorded_at(event["received_at"]),
    }


def device_payload_hash(event: dict[str, object]) -> str:
    payload = {
        **event,
        "recorded_at": event["recorded_at"].isoformat().replace("+00:00", "Z"),
        "received_at": event["received_at"].isoformat().replace("+00:00", "Z"),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def heartbeat_is_newer(recorded_at: datetime, event_id: str, current: dict[str, object] | None) -> bool:
    if current is None:
        return True
    return (recorded_at, event_id) > (current["last_seen"], str(current["event_id"]))


def is_offline_boundary(last_seen: datetime, now: datetime, offline_seconds: float) -> bool:
    return last_seen <= now - timedelta(seconds=offline_seconds)


def _episode_id(elderly_id: str, event_id: str) -> str:
    return f"device:{elderly_id}:{event_id}"


def persist_device_event(event: dict[str, object], database: Database) -> dict[str, object]:
    canonical = canonicalize_device_event(event)
    payload_hash = device_payload_hash(canonical)
    document = {**canonical, "payload_hash": payload_hash, "created_at": canonical["received_at"]}

    def callback(session: object) -> dict[str, object]:
        result = database.device_events.update_one(
            {"event_id": canonical["event_id"]},
            {"$setOnInsert": document},
            upsert=True,
            session=session,
        )
        if result.upserted_id is None:
            existing_event = database.device_events.find_one(
                {"event_id": canonical["event_id"]}, {"payload_hash": 1}, session=session
            )
            if existing_event is None or existing_event.get("payload_hash") != payload_hash:
                raise EventPayloadConflict(str(canonical["event_id"]))

        current = database.device_status.find_one(
            {"elderly_id": canonical["elderly_id"]}, session=session
        )
        if heartbeat_is_newer(
            canonical["received_at"], str(canonical["event_id"]), current
        ):
            if current and current.get("status") == "offline":
                database.alerts.update_one(
                    {
                        "elderly_id": canonical["elderly_id"],
                        "alert_type": "device_offline",
                        "episode_id": current.get("offline_episode_id"),
                        "status": {"$in": ["unresolved", "acknowledged"]},
                    },
                    {
                        "$set": {
                            "status": "resolved",
                            "resolved_at": canonical["received_at"],
                            "updated_at": canonical["received_at"],
                        }
                    },
                    session=session,
                )
            database.device_status.update_one(
                {"elderly_id": canonical["elderly_id"]},
                {
                    "$set": {
                        "event_id": canonical["event_id"],
                        "status": "online",
                        "last_seen": canonical["received_at"],
                        "updated_at": canonical["received_at"],
                    },
                    "$unset": {"offline_episode_id": ""},
                },
                upsert=True,
                session=session,
            )
            return {"event_id": canonical["event_id"], "status": "online", "latest": True}
        return {"event_id": canonical["event_id"], "status": current["status"], "latest": False}

    with database.client.start_session() as session:
        return session.with_transaction(callback)


def scan_offline_devices(
    database: Database,
    now: datetime,
    offline_seconds: float,
    batch_size: int = 100,
    lease_seconds: int = 60,
    clock: Callable[[], datetime] | None = None,
) -> int:
    if offline_seconds <= 0:
        raise ValueError("DEVICE_OFFLINE_SECONDS must be positive")
    if batch_size <= 0:
        raise ValueError("DEVICE_SCAN_BATCH_SIZE must be positive")
    if lease_seconds <= 0:
        raise ValueError("SCAN_LEASE_SECONDS must be positive")
    lease_owner = acquire_scan_lease(database, "device-offline", now, lease_seconds)
    if lease_owner is None:
        return 0
    cutoff = now - timedelta(seconds=offline_seconds)
    candidates = list(
        database.device_status.find(
            {"status": "online", "last_seen": {"$lte": cutoff}}
        )
        .sort([("last_seen", 1), ("elderly_id", 1)])
        .limit(batch_size)
    )
    changed = 0
    for candidate in candidates:
        episode_id = _episode_id(str(candidate["elderly_id"]), str(candidate["event_id"]))

        def callback(session: object) -> int:
            renewal_now = clock() if clock is not None else now
            if not renew_scan_lease(
                database,
                "device-offline",
                lease_owner,
                renewal_now,
                lease_seconds,
                session,
            ):
                raise ScanLeaseLost()
            result = database.device_status.update_one(
                {
                    "elderly_id": candidate["elderly_id"],
                    "event_id": candidate["event_id"],
                    "status": "online",
                    "last_seen": candidate["last_seen"],
                },
                {
                    "$set": {
                        "status": "offline",
                        "updated_at": now,
                        "offline_episode_id": episode_id,
                    }
                },
                session=session,
            )
            if result.modified_count == 0:
                return 0
            database.alerts.update_one(
                {
                    "elderly_id": candidate["elderly_id"],
                    "alert_type": "device_offline",
                    "episode_id": episode_id,
                },
                {
                    "$setOnInsert": {
                        "alert_id": derive_public_alert_id(candidate["event_id"], "device_offline"),
                        "event_id": candidate["event_id"],
                        "elderly_id": candidate["elderly_id"],
                        "alert_type": "device_offline",
                        "episode_id": episode_id,
                        "source": "device",
                        "severity": "warning",
                        "status": "unresolved",
                        "message": "Monitoring device is offline",
                        "created_at": now,
                    }
                },
                upsert=True,
                session=session,
            )
            return 1

        try:
            with database.client.start_session() as session:
                changed += session.with_transaction(callback)
        except ScanLeaseLost:
            return changed
    release_scan_lease(
        database, "device-offline", lease_owner, clock() if clock is not None else now
    )
    return changed


@celery_app.task(
    name="workers.device_worker.process_device_heartbeat",
    autoretry_for=TRANSIENT_DB_ERRORS,
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
    acks_late=True,
    reject_on_worker_lost=True,
)
def process_device_heartbeat(event: dict[str, object]) -> dict[str, object]:
    return persist_device_event(event, get_database())


@celery_app.task(
    name="workers.device_worker.scan_offline_devices",
    autoretry_for=TRANSIENT_DB_ERRORS,
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
    acks_late=True,
    reject_on_worker_lost=True,
)
def scan_offline_devices_task() -> int:
    threshold = float(os.environ.get("DEVICE_OFFLINE_SECONDS", "120"))
    if threshold <= 0:
        raise ValueError("DEVICE_OFFLINE_SECONDS must be positive")
    return scan_offline_devices(
        get_database(),
        datetime.now(UTC),
        threshold,
        int(os.environ.get("DEVICE_SCAN_BATCH_SIZE", "100")),
        int(os.environ.get("SCAN_LEASE_SECONDS", "60")),
        clock=lambda: datetime.now(UTC),
    )

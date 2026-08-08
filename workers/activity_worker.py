import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from typing import Callable
from uuid import uuid4

from pymongo import ReturnDocument
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from workers.celery_app import celery_app
from workers.database import get_database
from workers.notifications import enqueue_alert_notification
from workers.health_worker import (
    EventPayloadConflict,
    TRANSIENT_DB_ERRORS,
    derive_public_alert_id,
    parse_recorded_at,
)


class ScanLeaseLost(Exception):
    """Raised when a scanner no longer owns its unexpired lease."""


def canonicalize_activity_event(event: dict[str, object]) -> dict[str, object]:
    value = str(event["value"])
    if value not in {"active", "inactive"}:
        raise ValueError("value must be active or inactive")
    return {
        "event_id": str(event["event_id"]),
        "elderly_id": str(event["elderly_id"]),
        "value": value,
        "recorded_at": parse_recorded_at(event["recorded_at"]),
        "received_at": parse_recorded_at(event["received_at"]),
    }


def activity_payload_hash(event: dict[str, object]) -> str:
    payload = {
        **event,
        "recorded_at": event["recorded_at"].isoformat().replace("+00:00", "Z"),
        "received_at": event["received_at"].isoformat().replace("+00:00", "Z"),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def advance_activity_state(
    current: dict[str, object] | None,
    event: dict[str, object],
) -> tuple[dict[str, object], str | None]:
    received_at = event["received_at"]
    event_id = str(event["event_id"])
    if current is not None and (received_at, event_id) <= (
        current["received_at"],
        str(current["event_id"]),
    ):
        if (
            event["value"] == "inactive"
            and current["value"] == "inactive"
            and received_at < current["inactive_since"]
        ):
            return {**current, "inactive_since": received_at}, None
        return current, None

    resolve_episode = None
    if event["value"] == "inactive":
        continuing = current is not None and current["value"] == "inactive"
        inactive_since = current["inactive_since"] if continuing else received_at
        episode_id = (
            current["episode_id"]
            if continuing
            else f"activity:{event['elderly_id']}:{event_id}"
        )
        alerted_at = current.get("alerted_at") if continuing else None
    else:
        if current is not None and current["value"] == "inactive":
            resolve_episode = str(current["episode_id"])
        inactive_since = None
        episode_id = None
        alerted_at = None

    return {
        "elderly_id": event["elderly_id"],
        "event_id": event_id,
        "value": event["value"],
        "received_at": received_at,
        "inactive_since": inactive_since,
        "episode_id": episode_id,
        "alerted_at": alerted_at,
        "updated_at": received_at,
    }, resolve_episode


def activity_event_can_change_anchor(
    current: dict[str, object], event: dict[str, object]
) -> bool:
    if current["value"] != "inactive":
        return False
    event_order = (event["received_at"], str(event["event_id"]))
    anchor_floor = (current["inactive_since"], "")
    if event["value"] == "inactive":
        return event_order < anchor_floor
    return anchor_floor < event_order < (
        current["received_at"],
        str(current["event_id"]),
    )


def find_inactive_anchor(
    database: Database,
    elderly_id: str,
    current: dict[str, object],
    session: object,
) -> datetime:
    current_received_at = current["received_at"]
    current_event_id = str(current["event_id"])
    latest_active = next(
        database.activity_logs.find(
            {
                "elderly_id": elderly_id,
                "value": "active",
                "$or": [
                    {"received_at": {"$lt": current_received_at}},
                    {
                        "received_at": current_received_at,
                        "event_id": {"$lt": current_event_id},
                    },
                ],
            },
            {"received_at": 1, "event_id": 1},
            session=session,
        )
        .sort([("received_at", -1), ("event_id", -1)])
        .hint("activity_episode_history")
        .limit(1),
        None,
    )
    inactive_query: dict[str, object] = {
        "elderly_id": elderly_id,
        "value": "inactive",
    }
    current_upper_bound = [
        {"received_at": {"$lt": current_received_at}},
        {
            "received_at": current_received_at,
            "event_id": {"$lte": current_event_id},
        },
    ]
    if latest_active is not None:
        inactive_query["$and"] = [
            {
                "$or": [
                    {"received_at": {"$gt": latest_active["received_at"]}},
                    {
                        "received_at": latest_active["received_at"],
                        "event_id": {"$gt": str(latest_active["event_id"])},
                    },
                ]
            },
            {"$or": current_upper_bound},
        ]
    else:
        inactive_query["$or"] = current_upper_bound
    earliest_inactive = next(
        database.activity_logs.find(
            inactive_query,
            {"received_at": 1},
            session=session,
        )
        .sort([("received_at", 1), ("event_id", 1)])
        .hint("activity_episode_history")
        .limit(1),
        None,
    )
    if earliest_inactive is None:
        return current["inactive_since"]
    return earliest_inactive["received_at"]


def persist_activity_event(
    event: dict[str, object], database: Database, inactivity_seconds: float
) -> dict[str, object]:
    canonical = canonicalize_activity_event(event)
    payload_hash = activity_payload_hash(canonical)
    document = {
        **canonical,
        "payload_hash": payload_hash,
        "created_at": canonical["received_at"],
    }

    def callback(session: object) -> dict[str, object]:
        result = database.activity_logs.update_one(
            {"event_id": canonical["event_id"]},
            {"$setOnInsert": document},
            upsert=True,
            session=session,
        )
        if result.upserted_id is None:
            existing = database.activity_logs.find_one(
                {"event_id": canonical["event_id"]},
                {"payload_hash": 1},
                session=session,
            )
            if existing is None or existing.get("payload_hash") != payload_hash:
                raise EventPayloadConflict(str(canonical["event_id"]))
            return {"event_id": canonical["event_id"], "episodes": 0}

        current = database.activity_state.find_one(
            {"elderly_id": canonical["elderly_id"]}, session=session
        )
        next_state, _ = advance_activity_state(current, canonical)
        if (
            current is not None
            and (canonical["received_at"], str(canonical["event_id"]))
            <= (current["received_at"], str(current["event_id"]))
            and activity_event_can_change_anchor(current, canonical)
        ):
            next_state = {
                **current,
                "inactive_since": find_inactive_anchor(
                    database,
                    str(canonical["elderly_id"]),
                    current,
                    session,
                ),
            }
        if next_state is not current:
            database.activity_state.replace_one(
                {"elderly_id": canonical["elderly_id"]},
                next_state,
                upsert=True,
                session=session,
            )
        if (
            canonical["value"] == "active"
            and next_state is not current
            and next_state["value"] == "active"
        ):
            database.alerts.update_many(
                {
                    "elderly_id": canonical["elderly_id"],
                    "alert_type": "long_inactivity",
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
        return {"event_id": canonical["event_id"], "episodes": 0}

    with database.client.start_session() as session:
        return session.with_transaction(callback)


def acquire_scan_lease(
    database: Database, lease_name: str, now: datetime, lease_seconds: int
) -> str | None:
    owner = str(uuid4())
    try:
        lease = database.scan_leases.find_one_and_update(
            {
                "_id": lease_name,
                "$or": [{"expires_at": {"$lte": now}}, {"owner": owner}],
            },
            {
                "$set": {
                    "owner": owner,
                    "expires_at": now + timedelta(seconds=lease_seconds),
                }
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError:
        return None
    return owner if lease is not None and lease.get("owner") == owner else None


def renew_scan_lease(
    database: Database,
    lease_name: str,
    owner: str,
    now: datetime,
    lease_seconds: int,
    session: object | None = None,
) -> bool:
    result = database.scan_leases.update_one(
        {"_id": lease_name, "owner": owner, "expires_at": {"$gt": now}},
        {"$set": {"expires_at": now + timedelta(seconds=lease_seconds)}},
        session=session,
    )
    return result.matched_count == 1


def release_scan_lease(
    database: Database, lease_name: str, owner: str, now: datetime
) -> None:
    database.scan_leases.update_one(
        {"_id": lease_name, "owner": owner},
        {"$set": {"expires_at": now}},
    )


def scan_inactive_profiles(
    database: Database,
    now: datetime,
    inactivity_seconds: float,
    batch_size: int,
    lease_seconds: int,
    clock: Callable[[], datetime] | None = None,
) -> int:
    if inactivity_seconds <= 0:
        raise ValueError("ACTIVITY_INACTIVITY_SECONDS must be positive")
    if batch_size <= 0:
        raise ValueError("ACTIVITY_SCAN_BATCH_SIZE must be positive")
    if lease_seconds <= 0:
        raise ValueError("SCAN_LEASE_SECONDS must be positive")
    lease_owner = acquire_scan_lease(database, "inactivity", now, lease_seconds)
    if lease_owner is None:
        return 0
    cutoff = now - timedelta(seconds=inactivity_seconds)
    candidates = list(
        database.activity_state.find(
            {
                "value": "inactive",
                "inactive_since": {"$lte": cutoff},
                "alerted_at": None,
            }
        )
        .sort([("inactive_since", 1), ("elderly_id", 1)])
        .limit(batch_size)
    )
    created = 0
    for candidate in candidates:
        def callback(session: object) -> bool:
            renewal_now = clock() if clock is not None else now
            if not renew_scan_lease(
                database,
                "inactivity",
                lease_owner,
                renewal_now,
                lease_seconds,
                session,
            ):
                raise ScanLeaseLost()
            state_filter = {
                "elderly_id": candidate["elderly_id"],
                "value": "inactive",
                "episode_id": candidate["episode_id"],
                "inactive_since": {"$lte": cutoff},
                "alerted_at": None,
            }
            state = database.activity_state.find_one(state_filter, session=session)
            if state is None:
                return False
            alert_filter = {
                "elderly_id": state["elderly_id"],
                "alert_type": "long_inactivity",
                "episode_id": state["episode_id"],
            }
            database.alerts.update_one(
                alert_filter,
                {
                    "$setOnInsert": {
                        **alert_filter,
                        "alert_id": derive_public_alert_id(state["event_id"], "long_inactivity"),
                        "event_id": state["event_id"],
                        "severity": "warning",
                        "status": "unresolved",
                        "source": "activity",
                        "message": "Sustained inactivity detected",
                        "created_at": now,
                    }
                },
                upsert=True,
                session=session,
            )
            enqueue_alert_notification(
                database,
                alert_id=derive_public_alert_id(state["event_id"], "long_inactivity"),
                elderly_id=state["elderly_id"],
                session=session,
            )
            database.activity_state.update_one(
                state_filter, {"$set": {"alerted_at": now}}, session=session
            )
            return True

        try:
            with database.client.start_session() as session:
                created += bool(session.with_transaction(callback))
        except ScanLeaseLost:
            return created
    release_scan_lease(
        database, "inactivity", lease_owner, clock() if clock is not None else now
    )
    return created


@celery_app.task(
    name="workers.activity_worker.process_activity_data",
    autoretry_for=TRANSIENT_DB_ERRORS,
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
    acks_late=True,
    reject_on_worker_lost=True,
)
def process_activity_data(event: dict[str, object]) -> dict[str, object]:
    threshold = float(os.environ.get("ACTIVITY_INACTIVITY_SECONDS", "3600"))
    if threshold <= 0:
        raise ValueError("ACTIVITY_INACTIVITY_SECONDS must be positive")
    return persist_activity_event(event, get_database(), threshold)


@celery_app.task(
    name="workers.activity_worker.scan_inactive_profiles",
    autoretry_for=TRANSIENT_DB_ERRORS,
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
    acks_late=True,
    reject_on_worker_lost=True,
)
def scan_inactive_profiles_task() -> int:
    return scan_inactive_profiles(
        get_database(),
        datetime.now(UTC),
        float(os.environ.get("ACTIVITY_INACTIVITY_SECONDS", "3600")),
        int(os.environ.get("ACTIVITY_SCAN_BATCH_SIZE", "100")),
        int(os.environ.get("SCAN_LEASE_SECONDS", "60")),
        clock=lambda: datetime.now(UTC),
    )

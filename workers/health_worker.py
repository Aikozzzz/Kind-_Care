import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID, uuid5

from pymongo.database import Database
from pymongo.errors import (
    AutoReconnect,
    ConnectionFailure,
    NetworkTimeout,
    ServerSelectionTimeoutError,
    WTimeoutError,
)

from analysis.risk_analysis import analyze_health
from workers.celery_app import celery_app
from workers.database import get_database


TRANSIENT_DB_ERRORS = (
    AutoReconnect,
    ConnectionFailure,
    NetworkTimeout,
    ServerSelectionTimeoutError,
    WTimeoutError,
)
KINDCARE_ALERT_NAMESPACE = UUID("10445fb8-395a-5a09-8b80-839022bcc3db")


def derive_public_alert_id(event_id: object, alert_type: object) -> str:
    return str(uuid5(KINDCARE_ALERT_NAMESPACE, f"{event_id}\0{alert_type}"))


class EventPayloadConflict(Exception):
    def __init__(self, event_id: str) -> None:
        super().__init__(f"Event {event_id} was already used with a different payload")


def parse_recorded_at(value: object) -> datetime:
    if isinstance(value, datetime):
        recorded_at = value
    elif isinstance(value, str):
        recorded_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError("recorded_at must be an ISO 8601 datetime")
    if recorded_at.tzinfo is None or recorded_at.utcoffset() is None:
        raise ValueError("recorded_at must include a timezone")
    return recorded_at.astimezone(UTC)


def canonicalize_health_event(event: dict[str, object]) -> dict[str, object]:
    return {
        "event_id": str(event["event_id"]),
        "elderly_id": str(event["elderly_id"]),
        "heart_rate": int(event["heart_rate"]),
        "temperature": float(event["temperature"]),
        "oxygen_level": int(event["oxygen_level"]),
        "blood_pressure": event.get("blood_pressure"),
        "movement_status": str(event["movement_status"]),
        "medicine_status": str(event["medicine_status"]),
        "emergency_pressed": bool(event["emergency_pressed"]),
        "recorded_at": parse_recorded_at(event["recorded_at"]),
    }


def health_payload_hash(canonical_event: dict[str, object]) -> str:
    serializable = {
        **canonical_event,
        "recorded_at": canonical_event["recorded_at"].isoformat().replace(
            "+00:00", "Z"
        ),
    }
    payload = json.dumps(serializable, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def persist_health_event(
    event: dict[str, object],
    database: Database,
) -> dict[str, object]:
    canonical_event = canonicalize_health_event(event)
    assessment = analyze_health(canonical_event)
    event_id = canonical_event["event_id"]
    elderly_id = canonical_event["elderly_id"]
    payload_hash = health_payload_hash(canonical_event)
    created_at = datetime.now(UTC)
    health_document = {
        **canonical_event,
        "payload_hash": payload_hash,
        "risk_level": assessment.risk_level,
        "alert_count": len(assessment.findings),
        "created_at": created_at,
    }

    def transaction_callback(session: object) -> dict[str, object]:
        result = database.health_logs.update_one(
            {"event_id": event_id},
            {"$setOnInsert": health_document},
            upsert=True,
            session=session,
        )
        if result.upserted_id is None:
            existing = database.health_logs.find_one(
                {"event_id": event_id},
                {"payload_hash": 1, "risk_level": 1, "alert_count": 1},
                session=session,
            )
            if existing is None or existing.get("payload_hash") != payload_hash:
                raise EventPayloadConflict(event_id)
            return {
                "event_id": event_id,
                "risk_level": existing["risk_level"],
                "alerts_created": existing["alert_count"],
            }

        for finding in assessment.findings:
            database.alerts.insert_one(
                {
                    "alert_id": derive_public_alert_id(event_id, finding.alert_type),
                    "event_id": event_id,
                    "elderly_id": elderly_id,
                    "alert_type": finding.alert_type,
                    "severity": finding.severity,
                    "status": "unresolved",
                    "message": finding.message,
                    "created_at": created_at,
                },
                session=session,
            )

        return {
            "event_id": event_id,
            "risk_level": assessment.risk_level,
            "alerts_created": len(assessment.findings),
        }

    with database.client.start_session() as session:
        return session.with_transaction(transaction_callback)


@celery_app.task(
    name="workers.health_worker.process_health_data",
    autoretry_for=TRANSIENT_DB_ERRORS,
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
    acks_late=True,
    reject_on_worker_lost=True,
)
def process_health_data(event: dict[str, object]) -> dict[str, object]:
    return persist_health_event(event, get_database())

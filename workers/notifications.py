from datetime import UTC, datetime, timedelta


def enqueue_alert_notification(
    database: object,
    *,
    alert_id: str,
    elderly_id: str,
    session: object,
) -> None:
    """Create one retry-safe notification intent inside the alert transaction."""
    collection = getattr(database, "alert_notification_events", None)
    if collection is None:
        return
    collection.update_one(
        {"alert_id": alert_id, "notification_kind": "created"},
        {
            "$setOnInsert": {
                "notification_event_id": f"{alert_id}:created",
                "alert_id": alert_id,
                "elderly_id": elderly_id,
                "notification_kind": "created",
                "status": "pending",
                "attempt_count": 0,
                "next_attempt_at": datetime.now(UTC),
                "created_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
            }
        },
        upsert=True,
        session=session,
    )


def retry_at(attempt_count: int) -> datetime:
    seconds = min(60, 2 ** min(attempt_count, 6))
    return datetime.now(UTC) + timedelta(seconds=seconds)

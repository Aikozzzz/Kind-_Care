import os
from datetime import UTC, datetime, timedelta
from typing import Callable

from pymongo.database import Database

from workers.activity_worker import (
    ScanLeaseLost,
    acquire_scan_lease,
    release_scan_lease,
    renew_scan_lease,
)
from workers.celery_app import celery_app
from workers.database import get_database
from workers.health_worker import TRANSIENT_DB_ERRORS, derive_public_alert_id


def missed_boundary(
    scheduled_for: datetime, now: datetime, grace_seconds: float
) -> bool:
    return scheduled_for <= now - timedelta(seconds=grace_seconds)


def scan_missed_reminders(
    database: Database,
    now: datetime,
    grace_seconds: float,
    batch_size: int,
    lease_seconds: int,
    clock: Callable[[], datetime] | None = None,
) -> int:
    if grace_seconds <= 0:
        raise ValueError("REMINDER_GRACE_SECONDS must be positive")
    if batch_size <= 0:
        raise ValueError("REMINDER_SCAN_BATCH_SIZE must be positive")
    if lease_seconds <= 0:
        raise ValueError("SCAN_LEASE_SECONDS must be positive")
    owner = acquire_scan_lease(database, "missed-reminders", now, lease_seconds)
    if owner is None:
        return 0
    cutoff = now - timedelta(seconds=grace_seconds)
    candidates = list(
        database.reminders.find(
            {"status": "pending", "scheduled_for": {"$lte": cutoff}}
        )
        .sort([("scheduled_for", 1), ("reminder_id", 1)])
        .limit(batch_size)
    )
    changed = 0
    for candidate in candidates:
        reminder_id = str(candidate["reminder_id"])
        episode_id = f"reminder:{reminder_id}"

        def callback(session: object) -> int:
            renewal_now = clock() if clock is not None else now
            if not renew_scan_lease(
                database,
                "missed-reminders",
                owner,
                renewal_now,
                lease_seconds,
                session,
            ):
                raise ScanLeaseLost()
            result = database.reminders.update_one(
                {
                    "reminder_id": reminder_id,
                    "status": "pending",
                    "scheduled_for": {"$lte": cutoff},
                },
                {"$set": {"status": "missed", "updated_at": now}},
                session=session,
            )
            if result.modified_count == 0:
                return 0
            database.alerts.update_one(
                {
                    "elderly_id": candidate["elderly_id"],
                    "alert_type": "missed_reminder",
                    "episode_id": episode_id,
                },
                {
                    "$setOnInsert": {
                        "alert_id": derive_public_alert_id(reminder_id, "missed_reminder"),
                        "event_id": reminder_id,
                        "elderly_id": candidate["elderly_id"],
                        "alert_type": "missed_reminder",
                        "episode_id": episode_id,
                        "source": "reminder",
                        "severity": "warning",
                        "status": "unresolved",
                        "message": f"Missed medicine reminder: {candidate['medicine_name']}",
                        "created_at": now,
                        "updated_at": now,
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
        database, "missed-reminders", owner, clock() if clock is not None else now
    )
    return changed


@celery_app.task(
    name="workers.reminder_worker.scan_missed_reminders",
    autoretry_for=TRANSIENT_DB_ERRORS,
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
    acks_late=True,
    reject_on_worker_lost=True,
)
def scan_missed_reminders_task() -> int:
    grace_seconds = float(os.environ.get("REMINDER_GRACE_SECONDS", "300"))
    batch_size = int(os.environ.get("REMINDER_SCAN_BATCH_SIZE", "100"))
    lease_seconds = int(os.environ.get("SCAN_LEASE_SECONDS", "60"))
    if grace_seconds <= 0:
        raise ValueError("REMINDER_GRACE_SECONDS must be positive")
    if batch_size <= 0:
        raise ValueError("REMINDER_SCAN_BATCH_SIZE must be positive")
    if lease_seconds <= 0:
        raise ValueError("SCAN_LEASE_SECONDS must be positive")
    return scan_missed_reminders(
        get_database(),
        datetime.now(UTC),
        grace_seconds,
        batch_size,
        lease_seconds,
        clock=lambda: datetime.now(UTC),
    )

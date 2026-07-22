import os
from datetime import UTC, datetime
from typing import Callable

from kombu import Connection
from pymongo.database import Database

from workers.celery_app import celery_app
from workers.database import get_database
from workers.health_worker import TRANSIENT_DB_ERRORS


def record_service_heartbeat(database: Database, now: datetime) -> None:
    database.service_health.update_one(
        {"_id": "scheduled-worker"},
        {"$set": {"processed_at": now}},
        upsert=True,
    )


def check_broker() -> None:
    connection = Connection(
        os.environ.get(
            "RABBITMQ_URL",
            "amqp://kindcare:kindcare_dev_only@localhost:5672//",
        ),
        connect_timeout=3,
    )
    try:
        connection.ensure_connection(max_retries=0, timeout=3)
    finally:
        connection.release()


def check_service_health(
    database: Database,
    now: datetime,
    max_age_seconds: float,
    broker_check: Callable[[], None] = check_broker,
) -> tuple[bool, str]:
    try:
        database.command("ping")
    except Exception:
        return False, "mongodb unavailable"
    try:
        broker_check()
    except Exception:
        return False, "rabbitmq unavailable"
    heartbeat = database.service_health.find_one({"_id": "scheduled-worker"})
    if heartbeat is None:
        return False, "scheduled heartbeat missing"
    age = (now - heartbeat["processed_at"]).total_seconds()
    if age > max_age_seconds:
        return False, "scheduled heartbeat stale"
    return True, "healthy"


@celery_app.task(
    name="workers.service_health.record_service_heartbeat",
    autoretry_for=TRANSIENT_DB_ERRORS,
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
    acks_late=True,
    reject_on_worker_lost=True,
)
def record_service_heartbeat_task() -> None:
    record_service_heartbeat(get_database(), datetime.now(UTC))


def main() -> int:
    max_age = float(os.environ.get("SERVICE_HEARTBEAT_MAX_AGE_SECONDS", "60"))
    if max_age <= 0:
        print("unhealthy: SERVICE_HEARTBEAT_MAX_AGE_SECONDS must be positive")
        return 1
    healthy, reason = check_service_health(get_database(), datetime.now(UTC), max_age)
    print(f"{'healthy' if healthy else 'unhealthy'}: {reason}")
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())

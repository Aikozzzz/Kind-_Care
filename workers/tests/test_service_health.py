from datetime import UTC, datetime, timedelta

from workers.celery_app import celery_app
from workers.service_health import (
    check_service_health,
    record_service_heartbeat,
    record_service_heartbeat_task,
)


NOW = datetime(2026, 7, 17, 8, 0, tzinfo=UTC)


class Result:
    matched_count = 1


class HealthCollection:
    def __init__(self, document=None):
        self.document = document
        self.update = None

    def update_one(self, query, update, **options):
        self.update = (query, update, options)
        return Result()

    def find_one(self, query):
        return self.document


class Database:
    def __init__(self, document=None):
        self.service_health = HealthCollection(document)
        self.commands = []

    def command(self, name):
        self.commands.append(name)


def test_service_heartbeat_task_is_scheduled_and_reliable() -> None:
    assert record_service_heartbeat_task.name in celery_app.tasks
    assert record_service_heartbeat_task.acks_late is True
    schedule = celery_app.conf.beat_schedule["record-service-heartbeat"]
    assert schedule["task"] == "workers.service_health.record_service_heartbeat"
    assert schedule["schedule"] > 0


def test_record_service_heartbeat_upserts_processing_timestamp() -> None:
    database = Database()

    record_service_heartbeat(database, NOW)

    assert database.service_health.update == (
        {"_id": "scheduled-worker"},
        {"$set": {"processed_at": NOW}},
        {"upsert": True},
    )


def test_health_probe_checks_dependencies_and_recent_processing() -> None:
    database = Database({"processed_at": NOW - timedelta(seconds=20)})
    broker_checks = []

    healthy, reason = check_service_health(
        database,
        NOW,
        60,
        broker_check=lambda: broker_checks.append("checked"),
    )

    assert (healthy, reason) == (True, "healthy")
    assert database.commands == ["ping"]
    assert broker_checks == ["checked"]


def test_health_probe_exposes_missing_or_stale_scheduled_processing() -> None:
    missing = Database()
    stale = Database({"processed_at": NOW - timedelta(seconds=61)})

    assert check_service_health(missing, NOW, 60, broker_check=lambda: None) == (
        False,
        "scheduled heartbeat missing",
    )
    assert check_service_health(stale, NOW, 60, broker_check=lambda: None) == (
        False,
        "scheduled heartbeat stale",
    )

import os

from celery.signals import worker_init
from pymongo import DESCENDING, MongoClient
from pymongo.database import Database
from pymongo.errors import OperationFailure


_client: MongoClient | None = None


def get_database() -> Database:
    global _client
    if _client is None:
        _client = MongoClient(
            os.environ.get(
                "MONGO_URI",
                "mongodb://localhost:27017/?replicaSet=rs0",
            ),
            tz_aware=True,
        )
    database_name = os.environ.get("DATABASE_NAME", "kindcare_db")
    return _client[database_name]


def ensure_named_index(
    collection: object,
    keys: list[tuple[str, int]],
    name: str,
    **options: object,
) -> None:
    indexes = collection.index_information()
    existing = indexes.get(name)
    definition_matches = existing is not None and existing.get("key") == keys
    if definition_matches:
        definition_matches = all(existing.get(key) == value for key, value in options.items())
    if definition_matches and "partialFilterExpression" not in options:
        definition_matches = "partialFilterExpression" not in existing
    if existing is not None and not definition_matches:
        try:
            collection.drop_index(name)
        except OperationFailure as error:
            if error.code != 27:
                raise
    collection.create_index(keys, name=name, **options)


def create_indexes(database: Database) -> None:
    database.health_logs.create_index(
        "event_id",
        unique=True,
        name="unique_health_event_id",
    )
    ensure_named_index(
        database.health_logs,
        [("elderly_id", 1), ("recorded_at", DESCENDING), ("event_id", DESCENDING)],
        "health_history_latest",
    )
    database.alerts.create_index(
        [("event_id", 1), ("alert_type", 1)],
        unique=True,
        name="unique_event_alert_type",
    )
    ensure_named_index(
        database.alerts,
        [("alert_id", 1)],
        "unique_alert_id",
        unique=True,
    )
    ensure_named_index(
        database.alerts,
        [
            ("elderly_id", 1),
            ("created_at", DESCENDING),
            ("event_id", DESCENDING),
            ("alert_type", 1),
        ],
        "alert_history_latest",
    )
    ensure_named_index(
        database.activity_logs,
        [("event_id", 1)],
        "unique_activity_event_id",
        unique=True,
    )
    ensure_named_index(
        database.activity_logs,
        [("elderly_id", 1), ("received_at", DESCENDING), ("event_id", DESCENDING)],
        "activity_history_latest",
    )
    ensure_named_index(
        database.activity_logs,
        [
            ("elderly_id", 1),
            ("received_at", 1),
            ("created_at", DESCENDING),
            ("event_id", DESCENDING),
        ],
        "activity_history_legacy",
    )
    ensure_named_index(
        database.activity_logs,
        [
            ("elderly_id", 1),
            ("received_at", 1),
            ("created_at", 1),
            ("recorded_at", DESCENDING),
            ("event_id", DESCENDING),
        ],
        "activity_history_legacy_recorded",
    )
    ensure_named_index(
        database.activity_logs,
        [("elderly_id", 1), ("value", 1), ("received_at", 1), ("event_id", 1)],
        "activity_episode_history",
    )
    ensure_named_index(
        database.activity_state,
        [("elderly_id", 1)],
        "unique_activity_state_elderly_id",
        unique=True,
    )
    ensure_named_index(
        database.activity_state,
        [("value", 1), ("alerted_at", 1), ("inactive_since", 1), ("elderly_id", 1)],
        "activity_inactivity_scan",
    )
    ensure_named_index(
        database.device_events,
        [("event_id", 1)],
        "unique_device_event_id",
        unique=True,
    )
    ensure_named_index(
        database.device_events,
        [("elderly_id", 1), ("received_at", DESCENDING), ("event_id", DESCENDING)],
        "device_history_latest",
    )
    ensure_named_index(
        database.device_events,
        [
            ("elderly_id", 1),
            ("received_at", 1),
            ("created_at", DESCENDING),
            ("event_id", DESCENDING),
        ],
        "device_history_legacy",
    )
    ensure_named_index(
        database.device_events,
        [
            ("elderly_id", 1),
            ("received_at", 1),
            ("created_at", 1),
            ("recorded_at", DESCENDING),
            ("event_id", DESCENDING),
        ],
        "device_history_legacy_recorded",
    )
    ensure_named_index(
        database.device_status,
        [("elderly_id", 1)],
        "unique_device_status_elderly_id",
        unique=True,
    )
    ensure_named_index(
        database.device_status,
        [("status", 1), ("last_seen", 1)],
        "device_offline_scan",
    )
    ensure_named_index(
        database.alerts,
        [
            ("elderly_id", 1),
            ("status", 1),
            ("severity", 1),
            ("created_at", DESCENDING),
            ("event_id", DESCENDING),
            ("alert_type", 1),
        ],
        "alert_current_risk",
    )
    ensure_named_index(
        database.alerts,
        [("elderly_id", 1), ("alert_type", 1), ("episode_id", 1)],
        "unique_alert_episode",
        unique=True,
        partialFilterExpression={"episode_id": {"$exists": True}},
    )
    ensure_named_index(
        database.reminders,
        [("reminder_id", 1)],
        "unique_reminder_id",
        unique=True,
    )
    ensure_named_index(
        database.reminders,
        [("status", 1), ("scheduled_for", 1), ("reminder_id", 1)],
        "reminder_missed_scan",
    )
    notification_events = getattr(database, "alert_notification_events", None)
    if notification_events is not None:
        ensure_named_index(
            notification_events,
            [("alert_id", 1), ("notification_kind", 1)],
            "unique_alert_notification_event",
            unique=True,
        )
        ensure_named_index(
            notification_events,
            [("status", 1), ("next_attempt_at", 1)],
            "notification_delivery_queue",
        )
    deliveries = getattr(database, "telegram_deliveries", None)
    if deliveries is not None:
        ensure_named_index(
            deliveries,
            [("notification_event_id", 1), ("telegram_user_id", 1)],
            "unique_telegram_delivery",
            unique=True,
        )


@worker_init.connect
def initialize_worker_database(**kwargs: object) -> None:
    global _client
    try:
        get_database().command("ping")
    finally:
        if _client is not None:
            _client.close()
            _client = None

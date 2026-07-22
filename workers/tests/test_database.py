import pytest

import workers.database as worker_database
from workers.database import create_indexes, initialize_worker_database


class RecordingCollection:
    def __init__(self, indexes: dict[str, dict[str, object]] | None = None) -> None:
        self.indexes = indexes or {}
        self.dropped: list[str] = []
        self.created: list[tuple[object, dict[str, object]]] = []

    def index_information(self) -> dict[str, dict[str, object]]:
        return self.indexes

    def drop_index(self, name: str) -> None:
        self.dropped.append(name)

    def create_index(self, keys: object, **options: object) -> None:
        self.created.append((keys, options))


class RecordingDatabase:
    def __init__(self) -> None:
        self.health_logs = RecordingCollection(
            {"health_history_latest": {"key": [("elderly_id", 1), ("recorded_at", -1)]}}
        )
        self.alerts = RecordingCollection(
            {"alert_history_latest": {"key": [("elderly_id", 1), ("created_at", -1)]}}
        )
        self.activity_logs = RecordingCollection()
        self.activity_state = RecordingCollection()
        self.device_events = RecordingCollection()
        self.device_status = RecordingCollection()
        self.reminders = RecordingCollection()


def test_worker_startup_checks_connectivity_without_creating_indexes(monkeypatch) -> None:
    commands = []

    class Database:
        def command(self, name):
            commands.append(name)

    monkeypatch.setattr(worker_database, "get_database", lambda: Database())
    monkeypatch.setattr(
        worker_database,
        "create_indexes",
        lambda database: pytest.fail("worker startup must not create indexes"),
    )

    initialize_worker_database()

    assert commands == ["ping"]


def test_create_indexes_replaces_stale_named_history_indexes() -> None:
    database = RecordingDatabase()

    create_indexes(database)

    assert database.health_logs.dropped == ["health_history_latest"]
    assert database.alerts.dropped == ["alert_history_latest"]
    episode = database.alerts.created[-1]
    assert episode == (
        [("elderly_id", 1), ("alert_type", 1), ("episode_id", 1)],
        {
            "unique": True,
            "partialFilterExpression": {"episode_id": {"$exists": True}},
            "name": "unique_alert_episode",
        },
    )


def test_create_indexes_preserves_existing_full_unique_alert_id_index() -> None:
    database = RecordingDatabase()
    database.alerts.indexes["unique_alert_id"] = {
        "key": [("alert_id", 1)],
        "unique": True,
    }

    create_indexes(database)

    assert "unique_alert_id" not in database.alerts.dropped
    alert_id_call = next(
        call for call in database.alerts.created if call[1].get("name") == "unique_alert_id"
    )
    assert alert_id_call == (
        [("alert_id", 1)],
        {"unique": True, "name": "unique_alert_id"},
    )


def test_create_indexes_promotes_existing_partial_alert_id_index() -> None:
    database = RecordingDatabase()
    database.alerts.indexes["unique_alert_id"] = {
        "key": [("alert_id", 1)],
        "unique": True,
        "partialFilterExpression": {"alert_id": {"$type": "string"}},
    }

    create_indexes(database)

    assert "unique_alert_id" in database.alerts.dropped


def test_create_indexes_replaces_stale_episode_index_options() -> None:
    database = RecordingDatabase()
    database.alerts.indexes["unique_alert_episode"] = {
        "key": [("elderly_id", 1), ("alert_type", 1), ("episode_id", 1)],
        "unique": False,
    }

    create_indexes(database)

    assert database.alerts.dropped == ["alert_history_latest", "unique_alert_episode"]


def test_create_indexes_adds_received_time_state_and_scan_indexes_safely() -> None:
    database = RecordingDatabase()

    create_indexes(database)

    assert database.activity_logs.created[1] == (
        [("elderly_id", 1), ("received_at", -1), ("event_id", -1)],
        {"name": "activity_history_latest"},
    )
    assert database.activity_logs.created[2] == (
        [
            ("elderly_id", 1),
            ("received_at", 1),
            ("created_at", -1),
            ("event_id", -1),
        ],
        {"name": "activity_history_legacy"},
    )
    assert database.activity_logs.created[3] == (
        [
            ("elderly_id", 1),
            ("received_at", 1),
            ("created_at", 1),
            ("recorded_at", -1),
            ("event_id", -1),
        ],
        {"name": "activity_history_legacy_recorded"},
    )
    assert database.activity_logs.created[4] == (
        [("elderly_id", 1), ("value", 1), ("received_at", 1), ("event_id", 1)],
        {"name": "activity_episode_history"},
    )
    assert database.device_events.created[1] == (
        [("elderly_id", 1), ("received_at", -1), ("event_id", -1)],
        {"name": "device_history_latest"},
    )
    assert database.device_events.created[2] == (
        [
            ("elderly_id", 1),
            ("received_at", 1),
            ("created_at", -1),
            ("event_id", -1),
        ],
        {"name": "device_history_legacy"},
    )
    assert database.device_events.created[3] == (
        [
            ("elderly_id", 1),
            ("received_at", 1),
            ("created_at", 1),
            ("recorded_at", -1),
            ("event_id", -1),
        ],
        {"name": "device_history_legacy_recorded"},
    )
    assert database.activity_state.created == [
        (
            [("elderly_id", 1)],
            {"unique": True, "name": "unique_activity_state_elderly_id"},
        ),
        (
            [("value", 1), ("alerted_at", 1), ("inactive_since", 1), ("elderly_id", 1)],
            {"name": "activity_inactivity_scan"},
        ),
    ]
    assert (
        [
            ("elderly_id", 1),
            ("status", 1),
            ("severity", 1),
            ("created_at", -1),
            ("event_id", -1),
            ("alert_type", 1),
        ],
        {"name": "alert_current_risk"},
    ) in database.alerts.created

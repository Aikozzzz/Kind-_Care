from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from app.database import (
    create_indexes,
    database_lifespan,
    derive_reconstructed_activity_state,
    migrate_received_at,
    migrate_alert_ids,
)
from app.dependencies import get_dashboard_hub


class RecordingCollection:
    def __init__(self, indexes: dict[str, dict[str, object]] | None = None) -> None:
        self.calls: list[tuple[object, dict[str, object]]] = []
        self.indexes = indexes or {}
        self.dropped: list[str] = []
        self.updates: list[tuple[object, object]] = []

    async def create_index(self, field: object, **options: object) -> None:
        self.calls.append((field, options))

    async def index_information(self) -> dict[str, dict[str, object]]:
        return self.indexes

    async def drop_index(self, name: str) -> None:
        self.dropped.append(name)

    async def update_many(self, query: object, update: object) -> None:
        self.updates.append((query, update))


class FakeDatabase:
    def __init__(self, stale_history_indexes: bool = False) -> None:
        self.elderly_profiles = RecordingCollection()
        health_indexes = (
            {"health_history_latest": {"key": [("elderly_id", 1), ("recorded_at", -1)]}}
            if stale_history_indexes
            else None
        )
        alert_indexes = (
            {"alert_history_latest": {"key": [("elderly_id", 1), ("created_at", -1)]}}
            if stale_history_indexes
            else None
        )
        self.health_logs = RecordingCollection(health_indexes)
        self.alerts = RecordingCollection(alert_indexes)
        self.health_idempotency = RecordingCollection()
        self.activity_idempotency = RecordingCollection()
        self.device_idempotency = RecordingCollection()
        self.activity_logs = RecordingCollection()
        self.activity_state = RecordingCollection()
        self.device_events = RecordingCollection()
        self.device_status = RecordingCollection()
        self.reminder_idempotency = RecordingCollection()
        self.reminders = RecordingCollection()

    async def command(self, name: str) -> None:
        assert name == "ping"


@pytest.mark.asyncio
async def test_create_indexes_creates_profile_health_and_alert_indexes() -> None:
    database = FakeDatabase()

    await create_indexes(database)

    assert database.elderly_profiles.calls == [
        (
            "elderly_id",
            {"unique": True, "name": "unique_elderly_id"},
        )
    ]
    assert database.health_logs.calls == [
        ("event_id", {"unique": True, "name": "unique_health_event_id"}),
        (
            [("elderly_id", 1), ("recorded_at", -1), ("event_id", -1)],
            {"name": "health_history_latest"},
        ),
    ]
    assert database.alerts.calls == [
        (
            [("event_id", 1), ("alert_type", 1)],
            {"unique": True, "name": "unique_event_alert_type"},
        ),
        (
            [("alert_id", 1)],
            {
                "unique": True,
                "name": "unique_alert_id",
            },
        ),
        (
            [
                ("elderly_id", 1),
                ("created_at", -1),
                ("event_id", -1),
                ("alert_type", 1),
            ],
            {"name": "alert_history_latest"},
        ),
        (
            [
                ("elderly_id", 1),
                ("status", 1),
                ("severity", 1),
                ("created_at", -1),
                ("event_id", -1),
                ("alert_type", 1),
            ],
            {"name": "alert_current_risk"},
        ),
        (
            [("elderly_id", 1), ("alert_type", 1), ("episode_id", 1)],
            {
                "unique": True,
                "partialFilterExpression": {"episode_id": {"$exists": True}},
                "name": "unique_alert_episode",
            },
        ),
    ]


@pytest.mark.asyncio
async def test_create_indexes_preserves_existing_full_unique_alert_id_index() -> None:
    database = FakeDatabase()
    database.alerts.indexes["unique_alert_id"] = {
        "key": [("alert_id", 1)],
        "unique": True,
    }

    await create_indexes(database)

    assert "unique_alert_id" not in database.alerts.dropped
    alert_id_call = next(
        call for call in database.alerts.calls if call[1].get("name") == "unique_alert_id"
    )
    assert alert_id_call == (
        [("alert_id", 1)],
        {"unique": True, "name": "unique_alert_id"},
    )


@pytest.mark.asyncio
async def test_create_indexes_promotes_existing_partial_alert_id_index() -> None:
    database = FakeDatabase()
    database.alerts.indexes["unique_alert_id"] = {
        "key": [("alert_id", 1)],
        "unique": True,
        "partialFilterExpression": {"alert_id": {"$type": "string"}},
    }

    await create_indexes(database)

    assert "unique_alert_id" in database.alerts.dropped
    assert database.health_idempotency.calls == [
        (
            [("elderly_id", 1), ("key_hash", 1)],
            {"unique": True, "name": "unique_health_idempotency_key"},
        )
    ]
    assert database.activity_logs.calls == [
        ([("event_id", 1)], {"unique": True, "name": "unique_activity_event_id"}),
        (
            [("elderly_id", 1), ("received_at", -1), ("event_id", -1)],
            {"name": "activity_history_latest"},
        ),
        (
            [
                ("elderly_id", 1),
                ("received_at", 1),
                ("created_at", -1),
                ("event_id", -1),
            ],
            {"name": "activity_history_legacy"},
        ),
        (
            [
                ("elderly_id", 1),
                ("received_at", 1),
                ("created_at", 1),
                ("recorded_at", -1),
                ("event_id", -1),
            ],
            {"name": "activity_history_legacy_recorded"},
        ),
        (
            [("elderly_id", 1), ("value", 1), ("received_at", 1), ("event_id", 1)],
            {"name": "activity_episode_history"},
        ),
    ]
    assert database.activity_idempotency.calls == [
        (
            [("elderly_id", 1), ("key_hash", 1)],
            {"unique": True, "name": "unique_activity_idempotency_key"},
        )
    ]
    assert database.activity_state.calls == [
        ([("elderly_id", 1)], {"unique": True, "name": "unique_activity_state_elderly_id"}),
        (
            [("value", 1), ("alerted_at", 1), ("inactive_since", 1), ("elderly_id", 1)],
            {"name": "activity_inactivity_scan"},
        ),
    ]
    assert database.device_events.calls == [
        ([("event_id", 1)], {"unique": True, "name": "unique_device_event_id"}),
        (
            [("elderly_id", 1), ("received_at", -1), ("event_id", -1)],
            {"name": "device_history_latest"},
        ),
        (
            [
                ("elderly_id", 1),
                ("received_at", 1),
                ("created_at", -1),
                ("event_id", -1),
            ],
            {"name": "device_history_legacy"},
        ),
        (
            [
                ("elderly_id", 1),
                ("received_at", 1),
                ("created_at", 1),
                ("recorded_at", -1),
                ("event_id", -1),
            ],
            {"name": "device_history_legacy_recorded"},
        ),
    ]
    assert database.device_idempotency.calls == [
        (
            [("elderly_id", 1), ("key_hash", 1)],
            {"unique": True, "name": "unique_device_idempotency_key"},
        )
    ]
    assert database.device_status.calls == [
        ([("elderly_id", 1)], {"unique": True, "name": "unique_device_status_elderly_id"}),
        ([("status", 1), ("last_seen", 1)], {"name": "device_offline_scan"}),
    ]


@pytest.mark.asyncio
async def test_received_at_migration_prefers_created_at_then_recorded_at() -> None:
    database = FakeDatabase()

    await migrate_received_at(database)

    expected = (
        {"received_at": {"$exists": False}},
        [{"$set": {"received_at": {"$ifNull": ["$created_at", "$recorded_at"]}}}],
    )
    assert database.activity_logs.updates == [expected]
    assert database.device_events.updates == [expected]


def test_reconstructed_activity_state_uses_latest_event_and_episode_anchor() -> None:
    received_at = datetime(2026, 7, 17, 8, 0, tzinfo=UTC)
    history = [
        {
            "elderly_id": "E947",
            "event_id": "active-1",
            "value": "active",
            "received_at": received_at,
        },
        {
            "elderly_id": "E947",
            "event_id": "inactive-1",
            "value": "inactive",
            "received_at": received_at.replace(minute=1),
        },
        {
            "elderly_id": "E947",
            "event_id": "inactive-2",
            "value": "inactive",
            "received_at": received_at.replace(minute=2),
        },
    ]

    state = derive_reconstructed_activity_state(history)

    assert state["event_id"] == "inactive-2"
    assert state["value"] == "inactive"
    assert state["inactive_since"] == history[1]["received_at"]
    assert state["episode_id"] == "activity:E947:inactive-1"
    assert state["alerted_at"] is None


def test_reconstructed_latest_active_has_no_inactive_episode() -> None:
    received_at = datetime(2026, 7, 17, 8, 0, tzinfo=UTC)
    state = derive_reconstructed_activity_state(
        [
            {
                "elderly_id": "E947",
                "event_id": "inactive-1",
                "value": "inactive",
                "received_at": received_at,
            },
            {
                "elderly_id": "E947",
                "event_id": "active-2",
                "value": "active",
                "received_at": received_at.replace(minute=1),
            },
        ]
    )

    assert state == {
        "elderly_id": "E947",
        "event_id": "active-2",
        "value": "active",
        "received_at": received_at.replace(minute=1),
        "inactive_since": None,
        "episode_id": None,
        "alerted_at": None,
        "updated_at": received_at.replace(minute=1),
    }


@pytest.mark.asyncio
async def test_create_indexes_replaces_stale_named_history_indexes() -> None:
    database = FakeDatabase(stale_history_indexes=True)

    await create_indexes(database)

    assert database.health_logs.dropped == ["health_history_latest"]
    assert database.alerts.dropped == ["alert_history_latest"]


@pytest.mark.asyncio
async def test_create_indexes_replaces_stale_episode_index_options() -> None:
    database = FakeDatabase()
    database.alerts.indexes["unique_alert_episode"] = {
        "key": [("elderly_id", 1), ("alert_type", 1), ("episode_id", 1)],
        "unique": False,
    }

    await create_indexes(database)

    assert database.alerts.dropped == ["unique_alert_episode"]


@pytest.mark.asyncio
async def test_database_lifespan_owns_single_dashboard_hub(monkeypatch) -> None:
    events: list[str] = []
    database = FakeDatabase()

    class FakeClient:
        def __getitem__(self, name: str) -> FakeDatabase:
            assert name == "kindcare_test"
            return database

        async def close(self) -> None:
            events.append("client-closed")

    class RecordingHub:
        def __init__(self, service: object, poll_interval: float) -> None:
            self.service = service
            self.poll_interval = poll_interval
            events.append("hub-created")

        async def close(self) -> None:
            events.append("hub-closed")

    settings = SimpleNamespace(
        mongo_uri="mongodb://example",
        database_name="kindcare_test",
        dashboard_recent_alert_limit=7,
        dashboard_upcoming_reminder_limit=5,
        dashboard_recent_reminder_limit=6,
        websocket_poll_interval=0.25,
    )
    monkeypatch.setattr("app.database.get_settings", lambda: settings)
    monkeypatch.setattr(
        "app.database.AsyncMongoClient", lambda *args, **kwargs: FakeClient()
    )
    monkeypatch.setattr("app.database.DashboardHub", RecordingHub, raising=False)
    original_migrate = migrate_received_at

    async def recording_migrate(target: object) -> None:
        events.append("migrated")
        await original_migrate(target)

    async def recording_history_indexes(target: object) -> None:
        events.append("history-indexed")

    async def recording_reconstruction(target: object) -> None:
        events.append("state-reconstructed")

    async def recording_alert_migration(target: object) -> None:
        events.append("alerts-migrated")

    monkeypatch.setattr("app.database.migrate_received_at", recording_migrate)
    monkeypatch.setattr(
        "app.database.create_telemetry_history_indexes", recording_history_indexes
    )
    monkeypatch.setattr(
        "app.database.reconstruct_activity_state", recording_reconstruction
    )
    monkeypatch.setattr("app.database.migrate_alert_ids", recording_alert_migration)

    async def recording_alert_validator(target: object) -> None:
        events.append("alerts-validated")

    monkeypatch.setattr(
        "app.database.enforce_alert_id_validator", recording_alert_validator
    )
    app = FastAPI()
    connection = SimpleNamespace(app=app)

    async with database_lifespan(app):
        hub = app.state.dashboard_hub
        assert get_dashboard_hub(connection) is hub
        assert get_dashboard_hub(connection) is hub
        assert hub.service.elderly_profiles is database.elderly_profiles
        assert hub.service.health_logs is database.health_logs
        assert hub.service.alerts is database.alerts
        assert hub.service.activity_logs is database.activity_logs
        assert hub.service.device_status is database.device_status
        assert hub.service.reminders is database.reminders
        assert hub.service.recent_alert_limit == 7
        assert hub.service.upcoming_reminder_limit == 5
        assert hub.service.recent_reminder_limit == 6
        assert hub.poll_interval == 0.25

    assert not hasattr(app.state, "dashboard_hub")
    assert events == [
        "migrated",
        "history-indexed",
        "state-reconstructed",
        "alerts-migrated",
        "alerts-validated",
        "hub-created",
        "hub-closed",
        "client-closed",
    ]

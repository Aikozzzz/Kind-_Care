from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.database import ACTIVITY_STATE_MIGRATION_ID, reconstruct_activity_state


BASE = datetime(2026, 7, 17, 8, 0, tzinfo=UTC)


class MigrationCursor:
    def __init__(self, documents, fail_after=None):
        self.documents = documents
        self.fail_after = fail_after
        self.index = 0
        self.sort_keys = None
        self.hint_name = None
        self.batch_size_value = None

    def sort(self, keys):
        self.sort_keys = keys
        return self

    def hint(self, name):
        self.hint_name = name
        return self

    def batch_size(self, value):
        self.batch_size_value = value
        return self

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.fail_after is not None and self.index == self.fail_after:
            raise RuntimeError("injected migration failure")
        if self.index >= len(self.documents):
            raise StopAsyncIteration
        document = self.documents[self.index]
        self.index += 1
        return document


class ActivityLogs:
    def __init__(self, documents, fail_after=None):
        self.documents = documents
        self.fail_after = fail_after
        self.find_calls = 0
        self.cursor = None

    def find(self, query, projection):
        self.find_calls += 1
        self.cursor = MigrationCursor(self.documents, self.fail_after)
        return self.cursor

    async def distinct(self, field):
        pytest.fail("migration must not materialize profile IDs with distinct")


class SchemaMigrations:
    def __init__(self, completed=False):
        self.completed = completed
        self.writes = []

    async def find_one(self, query, projection):
        return {"_id": ACTIVITY_STATE_MIGRATION_ID} if self.completed else None

    async def update_one(self, query, update, **options):
        self.writes.append((query, update, options))
        self.completed = True


class Alerts:
    def __init__(self, documents=None):
        self.documents = documents or []

    async def find_one(self, query, projection=None, *, sort=None):
        matches = [
            document
            for document in self.documents
            if document["elderly_id"] == query["elderly_id"]
            and document["alert_type"] == query["alert_type"]
            and document["status"] == query["status"]
        ]
        return min(
            matches,
            key=lambda document: (document["created_at"], document["episode_id"]),
            default=None,
        )

    async def update_many(self, query, update):
        for document in self.documents:
            if (
                document["elderly_id"] == query["elderly_id"]
                and document["alert_type"] == query["alert_type"]
                and document["status"] == query["status"]
            ):
                document.update(update["$set"])


class ActivityState:
    def __init__(self):
        self.documents = {}

    async def find_one(self, query):
        return self.documents.get(query["elderly_id"])

    async def insert_one(self, document):
        query_id = document["elderly_id"]
        stored = {**document, "_id": query_id}
        self.documents[query_id] = stored

    async def replace_one(self, query, replacement):
        elderly_id = replacement["elderly_id"]
        self.documents[elderly_id] = replacement
        return SimpleNamespace(modified_count=1)


class Database:
    def __init__(self, documents, alerts=None, completed=False, fail_after=None):
        self.activity_logs = ActivityLogs(documents, fail_after)
        self.schema_migrations = SchemaMigrations(completed)
        self.alerts = Alerts(alerts)
        self.activity_state = ActivityState()


def event(elderly_id, event_id, value, seconds):
    return {
        "elderly_id": elderly_id,
        "event_id": event_id,
        "value": value,
        "received_at": BASE + timedelta(seconds=seconds),
    }


@pytest.mark.asyncio
async def test_completed_state_migration_never_reads_history() -> None:
    database = Database([], completed=True)

    migrated = await reconstruct_activity_state(database, batch_size=2)

    assert migrated is False
    assert database.activity_logs.find_calls == 0
    assert database.schema_migrations.writes == []


@pytest.mark.asyncio
async def test_state_migration_streams_profiles_and_adopts_earliest_alert() -> None:
    history = [
        event("E947", "inactive-latest", "inactive", 20),
        event("E947", "inactive-origin", "inactive", 10),
        event("E947", "preceding-active", "active", 0),
    ]
    alerts = [
        {
            "elderly_id": "E947",
            "alert_type": "long_inactivity",
            "episode_id": "later-alert-origin",
            "status": "unresolved",
            "created_at": BASE + timedelta(seconds=40),
        },
        {
            "elderly_id": "E947",
            "alert_type": "long_inactivity",
            "episode_id": "earliest-alert-origin",
            "status": "unresolved",
            "created_at": BASE + timedelta(seconds=30),
        },
    ]
    database = Database(history, alerts)

    migrated = await reconstruct_activity_state(database, batch_size=2)

    state = database.activity_state.documents["E947"]
    assert migrated is True
    assert state["event_id"] == "inactive-latest"
    assert state["inactive_since"] == BASE + timedelta(seconds=10)
    assert state["episode_id"] == "earliest-alert-origin"
    assert state["alerted_at"] == BASE + timedelta(seconds=30)
    assert database.activity_logs.cursor.sort_keys == [
        ("elderly_id", 1),
        ("received_at", -1),
        ("event_id", -1),
    ]
    assert database.activity_logs.cursor.hint_name == "activity_history_latest"
    assert database.activity_logs.cursor.batch_size_value == 2
    assert database.schema_migrations.completed is True

    await reconstruct_activity_state(database, batch_size=2)

    assert database.activity_logs.find_calls == 1


@pytest.mark.asyncio
async def test_failed_state_migration_writes_no_marker_and_retry_is_idempotent() -> None:
    history = [
        event("E901", "e901-active", "active", 10),
        event("E902", "e902-active", "active", 10),
        event("E903", "e903-active", "active", 10),
    ]
    database = Database(history, fail_after=2)

    with pytest.raises(RuntimeError, match="injected migration failure"):
        await reconstruct_activity_state(database, batch_size=1)

    assert database.schema_migrations.completed is False
    assert database.schema_migrations.writes == []
    assert set(database.activity_state.documents) == {"E901"}

    database.activity_logs.fail_after = None
    migrated = await reconstruct_activity_state(database, batch_size=1)

    assert migrated is True
    assert set(database.activity_state.documents) == {"E901", "E902", "E903"}
    assert database.schema_migrations.completed is True

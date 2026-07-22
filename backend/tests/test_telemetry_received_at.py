from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from app.models.activity import ActivityEventCreate
from app.models.device import DeviceHeartbeatCreate
from app.services.activity import ActivityEventService
from app.services.device import DeviceEventService
from app.services.telemetry import bounded_received_at_documents


NOW = datetime(2026, 7, 17, 8, 0, tzinfo=UTC)


class Profiles:
    async def find_one(self, query, projection):
        return {"_id": "profile"}


class Reservations:
    def __init__(self):
        self.documents = {}

    async def find_one_and_update(self, query, update, **options):
        identity = (query["elderly_id"], query["key_hash"])
        self.documents.setdefault(identity, update["$setOnInsert"].copy())
        return self.documents[identity].copy()


class Cursor:
    def __init__(self, documents=None):
        self.documents = documents or []

    async def to_list(self, length):
        return self.documents[:length]

    def sort(self, keys):
        self.sort_keys = keys
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def hint(self, name):
        self.hint_name = name
        return self


class Logs:
    def __init__(self, documents=None, legacy_documents=None, recorded_only=None):
        self.cursor = Cursor(documents)
        self.normal = Cursor(documents)
        self.legacy_created = Cursor(legacy_documents)
        self.legacy_recorded = Cursor(recorded_only)
        self.pipeline = None
        self.queries = []

    async def aggregate(self, pipeline):
        self.pipeline = pipeline
        return self.cursor

    def find(self, query):
        self.queries.append(query)
        if query["received_at"] == {"$exists": True}:
            return self.normal
        if query["created_at"] == {"$exists": True}:
            return self.legacy_created
        return self.legacy_recorded


class Dispatcher:
    def __init__(self):
        self.events = []

    def dispatch(self, event):
        self.events.append(event)


class IndexedCursor:
    def __init__(self, documents):
        self.documents = documents
        self.sort_keys = None
        self.limit_value = None

    def sort(self, keys):
        self.sort_keys = keys
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def hint(self, name):
        self.hint_name = name
        return self

    async def to_list(self, length):
        return self.documents[:length]


class IndexedLogs:
    def __init__(self, normal, legacy_created, legacy_recorded):
        self.normal = IndexedCursor(normal)
        self.legacy_created = IndexedCursor(legacy_created)
        self.legacy_recorded = IndexedCursor(legacy_recorded)
        self.queries = []

    def find(self, query):
        self.queries.append(query)
        if query["received_at"] == {"$exists": True}:
            return self.normal
        if query["created_at"] == {"$exists": True}:
            return self.legacy_created
        return self.legacy_recorded


@pytest.mark.asyncio
async def test_bounded_received_at_reads_use_server_time_before_future_client_time() -> None:
    normal = [
        {"event_id": "normal", "received_at": NOW + timedelta(seconds=10)},
    ]
    legacy_created = [
        {
            "event_id": "created",
            "created_at": NOW,
            "recorded_at": NOW + timedelta(days=30),
        },
    ]
    legacy_recorded = [
        {"event_id": "recorded", "recorded_at": NOW + timedelta(seconds=5)},
    ]
    logs = IndexedLogs(normal, legacy_created, legacy_recorded)

    documents = await bounded_received_at_documents(logs, "E001", limit=3)

    assert [document["event_id"] for document in documents] == [
        "normal",
        "recorded",
        "created",
    ]
    assert documents[2]["received_at"] == legacy_created[0]["created_at"]
    assert logs.queries == [
        {"elderly_id": "E001", "received_at": {"$exists": True}},
        {
            "elderly_id": "E001",
            "received_at": {"$exists": False},
            "created_at": {"$exists": True},
        },
        {
            "elderly_id": "E001",
            "received_at": {"$exists": False},
            "created_at": {"$exists": False},
        },
    ]
    assert logs.normal.sort_keys == [("received_at", -1), ("event_id", -1)]
    assert logs.legacy_created.sort_keys == [("created_at", -1), ("event_id", -1)]
    assert logs.legacy_recorded.sort_keys == [
        ("recorded_at", -1),
        ("event_id", -1),
    ]
    assert (
        logs.normal.limit_value
        == logs.legacy_created.limit_value
        == logs.legacy_recorded.limit_value
        == 3
    )
    assert logs.normal.hint_name == "activity_history_latest"
    assert logs.legacy_created.hint_name == "activity_history_legacy"
    assert logs.legacy_recorded.hint_name == "activity_history_legacy_recorded"


@pytest.mark.asyncio
async def test_activity_received_at_is_server_generated_stable_and_orders_history() -> None:
    reservations = Reservations()
    logs = Logs()
    clock_values = iter([NOW, NOW + timedelta(seconds=10)])
    service = ActivityEventService(
        Profiles(), reservations, logs, Dispatcher(), clock=lambda: next(clock_values)
    )
    request = ActivityEventCreate(
        elderly_id="E001", value="inactive", recorded_at=NOW + timedelta(days=30)
    )
    event_id = UUID("00000000-0000-5000-8000-000000000001")

    first = await service.queue_event(request, event_id, "stable-key")
    second = await service.queue_event(request, event_id, "stable-key")
    await service.list_activity("E001", 10, 0)

    assert first.received_at == second.received_at == NOW
    assert first.recorded_at == NOW + timedelta(days=30)
    assert logs.pipeline is None
    assert logs.queries == [
        {"elderly_id": "E001", "received_at": {"$exists": True}},
        {
            "elderly_id": "E001",
            "received_at": {"$exists": False},
            "created_at": {"$exists": True},
        },
        {
            "elderly_id": "E001",
            "received_at": {"$exists": False},
            "created_at": {"$exists": False},
        },
    ]
    assert logs.normal.limit_value == logs.legacy_created.limit_value == 10
    assert logs.legacy_recorded.limit_value == 10


@pytest.mark.asyncio
async def test_device_received_at_ignores_past_client_time_and_is_retry_stable() -> None:
    reservations = Reservations()
    service = DeviceEventService(
        Profiles(), reservations, Logs(), Dispatcher(), clock=lambda: NOW
    )
    request = DeviceHeartbeatCreate(
        elderly_id="E001", recorded_at=NOW - timedelta(days=30)
    )
    event_id = UUID("00000000-0000-5000-8000-000000000002")

    first = await service.queue_event(request, event_id, "heartbeat-key")
    second = await service.queue_event(request, event_id, "heartbeat-key")

    assert first.received_at == second.received_at == NOW
    assert first.recorded_at == NOW - timedelta(days=30)


@pytest.mark.asyncio
@pytest.mark.parametrize("service_type", [ActivityEventService, DeviceEventService])
async def test_changed_telemetry_payload_is_rejected_before_dispatch(service_type) -> None:
    reservations = Reservations()
    dispatcher = Dispatcher()
    service = service_type(
        Profiles(), reservations, Logs(), dispatcher, clock=lambda: NOW
    )
    event_id = UUID("00000000-0000-5000-8000-000000000003")
    if service_type is ActivityEventService:
        first = ActivityEventCreate(
            elderly_id="E001", value="active", recorded_at=NOW
        )
        changed = ActivityEventCreate(
            elderly_id="E001", value="inactive", recorded_at=NOW
        )
    else:
        first = DeviceHeartbeatCreate(elderly_id="E001", recorded_at=NOW)
        changed = DeviceHeartbeatCreate(
            elderly_id="E001", recorded_at=NOW + timedelta(seconds=1)
        )

    await service.queue_event(first, event_id, "shared-key")

    with pytest.raises(Exception, match="different payload"):
        await service.queue_event(changed, event_id, "shared-key")
    assert len(dispatcher.events) == 1
    reservation = next(iter(reservations.documents.values()))
    assert isinstance(reservation["payload_hash"], str)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("service_type", "document"),
    [
        (
            ActivityEventService,
            {
                "event_id": "00000000-0000-4000-8000-000000000011",
                "elderly_id": "E001",
                "value": "inactive",
                "recorded_at": NOW - timedelta(minutes=2),
                "created_at": NOW,
            },
        ),
        (
            DeviceEventService,
            {
                "event_id": "00000000-0000-4000-8000-000000000012",
                "elderly_id": "E001",
                "recorded_at": NOW - timedelta(minutes=2),
                "created_at": NOW,
            },
        ),
    ],
)
async def test_history_reads_legacy_documents_during_rolling_migration(
    service_type, document
) -> None:
    logs = Logs([], [document])
    service = service_type(Profiles(), Reservations(), logs, Dispatcher())

    if service_type is ActivityEventService:
        records = await service.list_activity("E001", 10, 0)
    else:
        records = await service.list_events("E001", 10, 0)

    assert logs.pipeline is None
    assert logs.legacy_created.limit_value == 10
    assert records[0].received_at == document["created_at"]

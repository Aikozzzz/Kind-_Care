from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.models.health import HealthEventCreate
from app.services.health import HealthBrokerUnavailable, HealthEventService


class RecordingCursor:
    def __init__(self) -> None:
        self.sort_keys: list[tuple[str, int]] | None = None

    def sort(self, keys: list[tuple[str, int]]):
        self.sort_keys = keys
        return self

    def skip(self, offset: int):
        return self

    def limit(self, limit: int):
        return self

    async def to_list(self, length: int):
        return []


class RecordingCollection:
    def __init__(self) -> None:
        self.cursor = RecordingCursor()

    def find(self, query: dict[str, object]) -> RecordingCursor:
        return self.cursor


class ActiveProfiles:
    def __init__(self) -> None:
        self.active = True

    async def find_one(
        self,
        query: dict[str, object],
        projection: dict[str, int],
    ) -> dict[str, str]:
        return {"_id": "profile"} if self.active else None


class MemoryReservations:
    def __init__(self) -> None:
        self.documents: dict[tuple[str, str], dict[str, object]] = {}
        self.calls = 0

    async def find_one_and_update(
        self,
        query: dict[str, object],
        update: dict[str, dict[str, object]],
        *,
        upsert: bool,
        return_document: object,
    ) -> dict[str, object]:
        self.calls += 1
        identity = (str(query["elderly_id"]), str(query["key_hash"]))
        self.documents.setdefault(identity, update["$setOnInsert"].copy())
        return self.documents[identity].copy()

    async def find_one(self, query: dict[str, object]) -> dict[str, object] | None:
        identity = (str(query["elderly_id"]), str(query["key_hash"]))
        document = self.documents.get(identity)
        return document.copy() if document is not None else None


class RecordingDispatcher:
    def __init__(self) -> None:
        self.events: list[object] = []
        self.fail = False

    def dispatch(self, event: object) -> None:
        self.events.append(event)
        if self.fail:
            raise HealthBrokerUnavailable()


def health_request(**updates: object) -> HealthEventCreate:
    payload: dict[str, object] = {
        "elderly_id": "E001",
        "heart_rate": 80,
        "temperature": 36.7,
        "oxygen_level": 97,
        "movement_status": "active",
        "medicine_status": "taken",
    }
    payload.update(updates)
    return HealthEventCreate(**payload)


@pytest.mark.asyncio
async def test_health_and_alert_histories_use_complete_tie_break_sorts() -> None:
    health_logs = RecordingCollection()
    alerts = RecordingCollection()
    service = HealthEventService(object(), object(), health_logs, alerts, object())

    await service.list_health("E001", limit=10, offset=0)
    await service.list_alerts("E001", 10, 0, None, None)

    assert health_logs.cursor.sort_keys == [
        ("recorded_at", -1),
        ("event_id", -1),
    ]
    assert alerts.cursor.sort_keys == [
        ("created_at", -1),
        ("event_id", -1),
        ("alert_type", 1),
    ]


@pytest.mark.asyncio
async def test_omitted_timestamp_reuses_hashed_key_reservation() -> None:
    reservations = MemoryReservations()
    dispatcher = RecordingDispatcher()
    service = HealthEventService(
        ActiveProfiles(),
        reservations,
        RecordingCollection(),
        RecordingCollection(),
        dispatcher,
    )
    event_id = UUID("008b2d23-93e6-5ef5-b676-f629c63c8bbb")

    first = await service.queue_event(health_request(), event_id, "shared-key")
    second = await service.queue_event(health_request(), event_id, "shared-key")

    assert first.event_id == second.event_id == event_id
    assert first.recorded_at == second.recorded_at
    assert len(reservations.documents) == 1
    reservation = next(iter(reservations.documents.values()))
    assert reservation["event_id"] == str(event_id)
    assert reservation["recorded_at"] == first.recorded_at
    assert isinstance(reservation["payload_hash"], str)
    assert "shared-key" not in reservation.values()


@pytest.mark.asyncio
async def test_broker_failure_keeps_timestamp_for_later_retry() -> None:
    reservations = MemoryReservations()
    dispatcher = RecordingDispatcher()
    dispatcher.fail = True
    service = HealthEventService(
        ActiveProfiles(),
        reservations,
        RecordingCollection(),
        RecordingCollection(),
        dispatcher,
    )
    event_id = UUID("008b2d23-93e6-5ef5-b676-f629c63c8bbb")

    with pytest.raises(HealthBrokerUnavailable):
        await service.queue_event(health_request(), event_id, "shared-key")
    dispatcher.fail = False
    retried = await service.queue_event(health_request(), event_id, "shared-key")

    assert len(reservations.documents) == 1
    assert dispatcher.events[0].recorded_at == retried.recorded_at


@pytest.mark.asyncio
async def test_explicit_timestamp_remains_payload_data() -> None:
    reservations = MemoryReservations()
    dispatcher = RecordingDispatcher()
    service = HealthEventService(
        ActiveProfiles(),
        reservations,
        RecordingCollection(),
        RecordingCollection(),
        dispatcher,
    )
    event_id = UUID("008b2d23-93e6-5ef5-b676-f629c63c8bbb")
    recorded_at = datetime(2026, 7, 16, 10, 30, tzinfo=UTC)

    event = await service.queue_event(
        health_request(recorded_at=recorded_at),
        event_id,
        "shared-key",
    )

    assert event.recorded_at == recorded_at
    assert reservations.calls == 1


@pytest.mark.asyncio
async def test_changed_health_payload_is_rejected_before_dispatch() -> None:
    reservations = MemoryReservations()
    dispatcher = RecordingDispatcher()
    service = HealthEventService(
        ActiveProfiles(),
        reservations,
        RecordingCollection(),
        RecordingCollection(),
        dispatcher,
    )
    event_id = UUID("008b2d23-93e6-5ef5-b676-f629c63c8bbb")

    await service.queue_event(health_request(), event_id, "shared-key")

    with pytest.raises(Exception, match="different payload"):
        await service.queue_event(
            health_request(oxygen_level=91), event_id, "shared-key"
        )
    assert len(dispatcher.events) == 1


@pytest.mark.asyncio
async def test_same_payload_replay_precedes_active_profile_validation() -> None:
    profiles = ActiveProfiles()
    dispatcher = RecordingDispatcher()
    service = HealthEventService(
        profiles,
        MemoryReservations(),
        RecordingCollection(),
        RecordingCollection(),
        dispatcher,
    )
    event_id = UUID("008b2d23-93e6-5ef5-b676-f629c63c8bbb")

    first = await service.queue_event(health_request(), event_id, "shared-key")
    profiles.active = False
    replayed = await service.queue_event(health_request(), event_id, "shared-key")

    assert replayed.event_id == first.event_id
    assert replayed.recorded_at == first.recorded_at

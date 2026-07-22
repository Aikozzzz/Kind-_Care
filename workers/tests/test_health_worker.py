from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import pytest
from pymongo.errors import AutoReconnect, PyMongoError

from workers.celery_app import celery_app
from workers.health_worker import (
    EventPayloadConflict,
    canonicalize_health_event,
    health_payload_hash,
    persist_health_event,
    process_health_data,
)


EVENT_ID = "a3ce37d4-6d4a-4f4e-a5af-744fb1f35cf0"


def health_event(**updates: object) -> dict[str, object]:
    event: dict[str, object] = {
        "event_id": EVENT_ID,
        "elderly_id": "E001",
        "heart_rate": 80,
        "temperature": 36.7,
        "oxygen_level": 97,
        "blood_pressure": "120/80",
        "movement_status": "active",
        "medicine_status": "taken",
        "emergency_pressed": False,
        "recorded_at": "2026-07-16T10:30:00Z",
    }
    event.update(updates)
    return event


@dataclass
class UpdateResult:
    upserted_id: str | None


@dataclass
class WriteCall:
    operation: str
    document: dict[str, object]
    session: object


class RecordingCollection:
    def __init__(
        self,
        existing: dict[str, object] | None = None,
        inserts_new: bool = True,
    ) -> None:
        self.existing = existing
        self.inserts_new = inserts_new
        self.calls: list[WriteCall] = []

    def update_one(
        self,
        query: dict[str, object],
        update: dict[str, object],
        *,
        upsert: bool,
        session: object,
    ) -> UpdateResult:
        self.calls.append(WriteCall("update", update["$setOnInsert"], session))
        return UpdateResult(EVENT_ID if self.inserts_new else None)

    def find_one(
        self,
        query: dict[str, object],
        projection: dict[str, int],
        *,
        session: object,
    ) -> dict[str, object] | None:
        return self.existing

    def insert_one(self, document: dict[str, object], *, session: object) -> None:
        self.calls.append(WriteCall("insert", document, session))


class RecordingSession:
    def __init__(self) -> None:
        self.with_transaction_called = False
        self.callback_calls = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def with_transaction(self, callback):
        self.with_transaction_called = True
        self.callback_calls += 1
        return callback(self)


class RecordingClient:
    def __init__(self) -> None:
        self.session = RecordingSession()

    def start_session(self) -> RecordingSession:
        return self.session


class RecordingDatabase:
    def __init__(self, existing: dict[str, object] | None = None) -> None:
        self.client = RecordingClient()
        self.health_logs = RecordingCollection(
            existing=existing,
            inserts_new=existing is None,
        )
        self.alerts = RecordingCollection()


def test_canonical_payload_hash_normalizes_utc_and_changes_with_measurements() -> None:
    first = canonicalize_health_event(health_event())
    equivalent = canonicalize_health_event(
        health_event(recorded_at="2026-07-16T12:30:00+02:00")
    )
    changed = canonicalize_health_event(health_event(heart_rate=81))

    assert first["recorded_at"] == datetime.fromisoformat(
        "2026-07-16T10:30:00+00:00"
    )
    assert health_payload_hash(first) == health_payload_hash(equivalent)
    assert health_payload_hash(first) != health_payload_hash(changed)


def test_new_event_writes_health_and_alerts_in_one_transaction() -> None:
    database = RecordingDatabase()

    result = persist_health_event(
        health_event(oxygen_level=91, temperature=39),
        database,
    )

    assert result["risk_level"] == "emergency"
    assert result["alerts_created"] == 2
    assert database.client.session.with_transaction_called is True
    assert database.client.session.callback_calls == 1
    all_calls = database.health_logs.calls + database.alerts.calls
    assert len(database.health_logs.calls) == 1
    assert len(database.alerts.calls) == 2
    assert all(UUID(str(call.document["alert_id"])) for call in database.alerts.calls)
    assert all(call.session is database.client.session for call in all_calls)
    health_document = database.health_logs.calls[0].document
    assert health_document["payload_hash"] == health_payload_hash(
        canonicalize_health_event(health_event(oxygen_level=91, temperature=39))
    )


def test_identical_existing_event_is_idempotent_without_alert_writes() -> None:
    canonical = canonicalize_health_event(health_event(oxygen_level=91))
    database = RecordingDatabase(
        existing={
            "payload_hash": health_payload_hash(canonical),
            "risk_level": "emergency",
            "alert_count": 1,
        }
    )

    result = persist_health_event(health_event(oxygen_level=91), database)

    assert result == {
        "event_id": EVENT_ID,
        "risk_level": "emergency",
        "alerts_created": 1,
    }
    assert database.alerts.calls == []


def test_changed_payload_with_existing_event_id_is_rejected() -> None:
    original = canonicalize_health_event(health_event())
    database = RecordingDatabase(
        existing={
            "payload_hash": health_payload_hash(original),
            "risk_level": "normal",
            "alert_count": 0,
        }
    )

    with pytest.raises(EventPayloadConflict, match=EVENT_ID):
        persist_health_event(health_event(heart_rate=81), database)

    assert database.alerts.calls == []


def test_worker_task_and_app_use_reliable_json_configuration() -> None:
    assert process_health_data.name == "workers.health_worker.process_health_data"
    assert process_health_data.acks_late is True
    assert process_health_data.reject_on_worker_lost is True
    assert process_health_data.max_retries == 3
    assert AutoReconnect in process_health_data.autoretry_for
    assert PyMongoError not in process_health_data.autoretry_for
    assert EventPayloadConflict not in process_health_data.autoretry_for
    assert celery_app.conf.accept_content == ["json"]
    assert celery_app.conf.task_serializer == "json"
    assert celery_app.conf.result_serializer == "json"
    assert celery_app.conf.worker_prefetch_multiplier == 1
    assert celery_app.conf.worker_enable_remote_control is False
    assert celery_app.conf.broker_transport_options["confirm_publish"] is True


def test_event_id_remains_a_json_string_in_persistence() -> None:
    database = RecordingDatabase()
    persist_health_event(health_event(), database)

    stored = database.health_logs.calls[0].document
    assert stored["event_id"] == EVENT_ID
    assert UUID(stored["event_id"])

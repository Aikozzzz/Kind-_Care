import os
import time
from collections.abc import Iterator
from uuid import uuid4

import pytest
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError, OperationFailure

import workers.health_worker as health_worker
from workers.celery_app import celery_app
from workers.database import create_indexes
from workers.health_worker import (
    EventPayloadConflict,
    persist_health_event,
    process_health_data,
)


pytestmark = pytest.mark.integration
EVENT_ID = "a3ce37d4-6d4a-4f4e-a5af-744fb1f35cf0"


def event(**updates: object) -> dict[str, object]:
    data: dict[str, object] = {
        "event_id": EVENT_ID,
        "elderly_id": "E001",
        "heart_rate": 80,
        "temperature": 36.7,
        "oxygen_level": 97,
        "blood_pressure": "120/80",
        "movement_status": "active",
        "medicine_status": "taken",
        "emergency_pressed": False,
        "recorded_at": "2026-07-10T10:30:00Z",
    }
    data.update(updates)
    return data


def ensure_safe_test_database_name(database_name: str) -> None:
    if database_name == "kindcare_integration_test":
        return
    if database_name.startswith("kindcare_test_"):
        return
    raise ValueError(f"Refusing to drop non-test database: {database_name}")


class TransientOnceAlerts:
    def __init__(self, collection: object) -> None:
        self.collection = collection
        self.failed = False

    def insert_one(
        self,
        document: dict[str, object],
        *,
        session: object,
    ) -> object:
        result = self.collection.insert_one(document, session=session)
        if not self.failed:
            self.failed = True
            raise OperationFailure(
                "injected transient transaction error",
                code=251,
                details={"errorLabels": ["TransientTransactionError"]},
            )
        return result


class TransientOnceDatabase:
    def __init__(self, database: object) -> None:
        self.client = database.client
        self.health_logs = database.health_logs
        self.alerts = TransientOnceAlerts(database.alerts)


@pytest.fixture
def mongo_database() -> Iterator[object]:
    uri = os.environ.get(
        "MONGO_URI",
        "mongodb://mongodb:27017/?replicaSet=rs0",
    )
    database_name = os.environ.get("DATABASE_NAME", "kindcare_integration_test")
    ensure_safe_test_database_name(database_name)
    client = MongoClient(uri, tz_aware=True)
    client.drop_database(database_name)
    database = client[database_name]
    create_indexes(database)
    yield database
    ensure_safe_test_database_name(database_name)
    client.drop_database(database_name)
    client.close()


def test_worker_indexes_enforce_idempotency(mongo_database: object) -> None:
    health_indexes = mongo_database.health_logs.index_information()
    alert_indexes = mongo_database.alerts.index_information()

    assert health_indexes["unique_health_event_id"]["unique"] is True
    assert health_indexes["health_history_latest"]["key"] == [
        ("elderly_id", 1),
        ("recorded_at", -1),
        ("event_id", -1),
    ]
    assert alert_indexes["unique_event_alert_type"]["unique"] is True
    assert alert_indexes["alert_history_latest"]["key"] == [
        ("elderly_id", 1),
        ("created_at", -1),
        ("event_id", -1),
        ("alert_type", 1),
    ]


def test_normal_event_persists_without_alert(mongo_database: object, monkeypatch) -> None:
    monkeypatch.setattr(health_worker, "get_database", lambda: mongo_database)

    result = process_health_data.run(event())

    assert result["risk_level"] == "normal"
    assert mongo_database.health_logs.count_documents({"event_id": EVENT_ID}) == 1
    stored = mongo_database.health_logs.find_one({"event_id": EVENT_ID})
    assert stored["risk_level"] == "normal"
    assert mongo_database.alerts.count_documents({"event_id": EVENT_ID}) == 0


def test_emergency_event_persists_health_and_alerts(
    mongo_database: object,
    monkeypatch,
) -> None:
    monkeypatch.setattr(health_worker, "get_database", lambda: mongo_database)

    result = process_health_data.run(
        event(oxygen_level=91, temperature=39, emergency_pressed=True)
    )

    assert result["risk_level"] == "emergency"
    stored = mongo_database.health_logs.find_one({"event_id": EVENT_ID})
    assert stored["risk_level"] == "emergency"
    alerts = list(mongo_database.alerts.find({"event_id": EVENT_ID}))
    assert {alert["alert_type"] for alert in alerts} == {
        "low_oxygen_level",
        "high_temperature",
        "emergency_button",
    }
    assert all(alert["status"] == "unresolved" for alert in alerts)


def test_duplicate_event_is_idempotent(mongo_database: object, monkeypatch) -> None:
    monkeypatch.setattr(health_worker, "get_database", lambda: mongo_database)
    duplicate = event(oxygen_level=91, emergency_pressed=True)

    process_health_data.run(duplicate)
    process_health_data.run(duplicate)

    assert mongo_database.health_logs.count_documents({"event_id": EVENT_ID}) == 1
    assert mongo_database.alerts.count_documents({"event_id": EVENT_ID}) == 2


def test_changed_payload_with_same_event_id_is_rejected(
    mongo_database: object,
    monkeypatch,
) -> None:
    monkeypatch.setattr(health_worker, "get_database", lambda: mongo_database)
    process_health_data.run(event())

    with pytest.raises(EventPayloadConflict, match=EVENT_ID):
        process_health_data.run(event(heart_rate=81))

    stored = mongo_database.health_logs.find_one({"event_id": EVENT_ID})
    assert stored["heart_rate"] == 80


def test_alert_write_failure_rolls_back_health_and_alerts(
    mongo_database: object,
    monkeypatch,
) -> None:
    monkeypatch.setattr(health_worker, "get_database", lambda: mongo_database)
    mongo_database.alerts.create_index(
        "message",
        unique=True,
        name="injected_message_failure",
    )
    mongo_database.alerts.insert_one({"message": "Low oxygen level detected"})

    with pytest.raises(DuplicateKeyError):
        process_health_data.run(event(oxygen_level=91))

    assert mongo_database.health_logs.count_documents({"event_id": EVENT_ID}) == 0
    assert mongo_database.alerts.count_documents({"event_id": EVENT_ID}) == 0


def test_transient_callback_failure_retries_whole_transaction(
    mongo_database: object,
) -> None:
    database = TransientOnceDatabase(mongo_database)

    result = persist_health_event(event(oxygen_level=91), database)

    assert database.alerts.failed is True
    assert result == {
        "event_id": EVENT_ID,
        "risk_level": "emergency",
        "alerts_created": 1,
    }
    assert mongo_database.health_logs.count_documents({"event_id": EVENT_ID}) == 1
    assert mongo_database.alerts.count_documents({"event_id": EVENT_ID}) == 1


def test_confirmed_rabbitmq_task_reaches_live_worker(mongo_database: object) -> None:
    event_id = str(uuid4())
    queued_event = event(
        event_id=event_id,
        oxygen_level=91,
        emergency_pressed=True,
    )

    celery_app.send_task(
        "workers.health_worker.process_health_data",
        args=[queued_event],
        queue="kindcare-integration",
        serializer="json",
        retry=True,
        retry_policy={
            "max_retries": 3,
            "interval_start": 0,
            "interval_step": 0.2,
            "interval_max": 0.5,
        },
    )

    deadline = time.monotonic() + 20
    stored = None
    while time.monotonic() < deadline:
        stored = mongo_database.health_logs.find_one({"event_id": event_id})
        if stored is not None:
            break
        time.sleep(0.2)

    assert stored is not None
    assert stored["risk_level"] == "emergency"
    assert mongo_database.alerts.count_documents({"event_id": event_id}) == 2

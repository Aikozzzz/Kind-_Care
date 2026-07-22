import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from pymongo import MongoClient

from app.main import app
from workers.database import create_indexes
from workers.activity_worker import acquire_scan_lease
from workers.reminder_worker import scan_missed_reminders


pytestmark = pytest.mark.integration
NOW = datetime(2026, 7, 18, 8, 0, tzinfo=UTC)


@pytest.fixture
def mongo_database() -> Iterator[object]:
    uri = os.environ.get("MONGO_URI", "mongodb://mongodb:27017/?replicaSet=rs0")
    name = os.environ.get("DATABASE_NAME", "kindcare_integration_test")
    if name != "kindcare_integration_test" and not name.startswith("kindcare_test_"):
        raise ValueError(f"Refusing to drop non-test database: {name}")
    client = MongoClient(uri, tz_aware=True)
    client.drop_database(name)
    database = client[name]
    create_indexes(database)
    yield database
    client.drop_database(name)
    client.close()


def reminder(reminder_id: str, seconds_ago: int = 61) -> dict[str, object]:
    return {
        "reminder_id": reminder_id,
        "elderly_id": "E901",
        "medicine_name": "Aspirin",
        "scheduled_for": NOW - timedelta(seconds=seconds_ago),
        "status": "pending",
        "created_at": NOW - timedelta(minutes=5),
        "updated_at": NOW - timedelta(minutes=5),
    }


def test_scanner_is_bounded_overlap_safe_and_retry_idempotent(mongo_database) -> None:
    mongo_database.reminders.insert_many([reminder(f"r-{index}") for index in range(3)])
    assert scan_missed_reminders(mongo_database, NOW, 60, 2, 30) == 2
    assert mongo_database.reminders.count_documents({"status": "missed"}) == 2
    assert mongo_database.alerts.count_documents({"alert_type": "missed_reminder"}) == 2
    assert scan_missed_reminders(mongo_database, NOW, 60, 2, 30) == 1
    assert scan_missed_reminders(mongo_database, NOW, 60, 2, 30) == 0
    assert mongo_database.alerts.count_documents({"alert_type": "missed_reminder"}) == 3


def test_scanner_respects_held_lease_and_recovers_after_expiry(mongo_database) -> None:
    mongo_database.reminders.insert_one(reminder("lease"))
    assert acquire_scan_lease(mongo_database, "missed-reminders", NOW, 30) is not None
    assert scan_missed_reminders(mongo_database, NOW, 60, 1, 30) == 0
    mongo_database.scan_leases.update_one(
        {"_id": "missed-reminders"}, {"$set": {"expires_at": NOW}}
    )
    assert scan_missed_reminders(mongo_database, NOW, 60, 1, 30) == 1


def test_taken_scanner_race_converges_without_unresolved_false_alert(mongo_database) -> None:
    reminder_id = "a74cfda8-d0ef-518e-a671-a2eabca7f6b0"
    mongo_database.reminders.insert_one(reminder(reminder_id))
    barrier = Barrier(2)

    def take() -> None:
        barrier.wait()
        response = api_client.patch(
            f"/api/reminders/{reminder_id}",
            json={"elderly_id": "E901", "status": "taken"},
        )
        assert response.status_code == 200, response.text

    def scan() -> None:
        barrier.wait()
        scan_missed_reminders(mongo_database, NOW, 60, 1, 30)

    with TestClient(app) as api_client:
        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(lambda operation: operation(), (take, scan)))

    assert mongo_database.reminders.find_one({"reminder_id": reminder_id})["status"] == "taken"
    assert mongo_database.alerts.count_documents(
        {"episode_id": f"reminder:{reminder_id}", "status": "unresolved"}
    ) == 0

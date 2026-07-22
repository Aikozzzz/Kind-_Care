from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_reminder_service
from app.main import app
from app.models.reminder import ReminderCreate, ReminderRecord
from app.services.elderly import ElderlyProfileNotFound
from app.services.reminder import (
    ReminderConflict,
    ReminderNotFound,
    ReminderStorageUnavailable,
)


NOW = datetime(2026, 7, 18, 8, 0, tzinfo=UTC)


class FakeReminderService:
    def __init__(self) -> None:
        self.error: Exception | None = None
        self.calls: list[object] = []

    async def create(self, request: ReminderCreate, idempotency_key: str) -> ReminderRecord:
        if self.error:
            raise self.error
        self.calls.append((request, idempotency_key))
        return reminder_record(request.elderly_id)

    async def list(self, elderly_id: str, limit: int, offset: int, reminder_status: str | None):
        self.calls.append((elderly_id, limit, offset, reminder_status))
        return [reminder_record(elderly_id)]

    async def mark_taken(self, reminder_id: str, elderly_id: str) -> ReminderRecord:
        if self.error:
            raise self.error
        self.calls.append((reminder_id, elderly_id))
        return reminder_record(elderly_id, reminder_id=reminder_id, status="taken")


def reminder_record(
    elderly_id: str,
    *,
    reminder_id: str = "7acdc1d0-0e14-54ce-bc9f-25c10297e6b7",
    status: str = "pending",
) -> ReminderRecord:
    return ReminderRecord(
        reminder_id=UUID(reminder_id),
        elderly_id=elderly_id,
        medicine_name="Aspirin",
        scheduled_for=NOW,
        status=status,
        created_at=NOW,
        updated_at=NOW,
        taken_at=NOW if status == "taken" else None,
    )


@pytest.fixture
def reminder_service(client: TestClient):
    service = FakeReminderService()
    app.dependency_overrides[get_reminder_service] = lambda: service
    yield service


def test_create_reminder_requires_key_and_returns_stable_created_record(
    client: TestClient, reminder_service: FakeReminderService
) -> None:
    payload = {
        "elderly_id": "E001",
        "medicine_name": "Aspirin",
        "scheduled_for": NOW.isoformat(),
    }
    assert client.post("/api/reminders", json=payload).status_code == 422
    first = client.post("/api/reminders", json=payload, headers={"Idempotency-Key": "dose-1"})
    second = client.post("/api/reminders", json=payload, headers={"Idempotency-Key": "dose-1"})

    assert first.status_code == second.status_code == 201
    assert first.json()["data"]["reminder_id"] == second.json()["data"]["reminder_id"]
    assert first.json()["data"]["status"] == "pending"


def test_reminder_history_is_bounded_and_status_filtered(
    client: TestClient, reminder_service: FakeReminderService
) -> None:
    response = client.get("/api/reminders/E001?limit=7&offset=2&status=missed")
    assert response.status_code == 200
    assert reminder_service.calls == [("E001", 7, 2, "missed")]
    assert client.get("/api/reminders/E001?limit=101").status_code == 422
    assert client.get("/api/reminders/E001?status=unknown").status_code == 422


def test_mark_taken_is_idempotent_api_action(
    client: TestClient, reminder_service: FakeReminderService
) -> None:
    reminder_id = "7acdc1d0-0e14-54ce-bc9f-25c10297e6b7"
    response = client.patch(
        f"/api/reminders/{reminder_id}",
        json={"elderly_id": "E001", "status": "taken"},
    )
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "taken"
    assert reminder_service.calls == [(reminder_id, "E001")]


@pytest.mark.parametrize(
    "error,expected",
    [
        (ElderlyProfileNotFound("E404"), 404),
        (ReminderNotFound("missing"), 404),
        (ReminderConflict("conflict"), 409),
        (ReminderStorageUnavailable(), 503),
    ],
)
def test_reminder_failures_use_clear_envelopes(
    client: TestClient,
    reminder_service: FakeReminderService,
    error: Exception,
    expected: int,
) -> None:
    reminder_service.error = error
    response = client.patch(
        "/api/reminders/7acdc1d0-0e14-54ce-bc9f-25c10297e6b7",
        json={"elderly_id": "E001", "status": "taken"},
    )
    assert response.status_code == expected
    assert response.json()["success"] is False
    assert set(response.json()) == {"success", "message", "data"}

from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_activity_service
from app.main import app
from app.models.activity import ActivityEvent, ActivityEventCreate, ActivityRecord
from app.services.activity import ActivityBrokerUnavailable, ActivityStorageUnavailable
from app.services.elderly import ElderlyProfileNotFound
from app.services.health import derive_health_event_id
from app.services.idempotency import IdempotencyConflict


NOW = datetime(2026, 7, 17, 8, 0, tzinfo=UTC)


class FakeActivityService:
    def __init__(self) -> None:
        self.error: Exception | None = None
        self.calls: list[object] = []

    async def queue_event(self, request: ActivityEventCreate, event_id: UUID, idempotency_key: str) -> ActivityEvent:
        if self.error:
            raise self.error
        self.calls.append((request, event_id, idempotency_key))
        return ActivityEvent(event_id=event_id, received_at=NOW, **request.model_dump())

    async def list_activity(self, elderly_id: str, limit: int, offset: int) -> list[ActivityRecord]:
        self.calls.append((elderly_id, limit, offset))
        event = ActivityEvent(event_id=UUID(int=1), elderly_id=elderly_id, value="active", recorded_at=NOW, received_at=NOW)
        return [ActivityRecord(**event.model_dump(), created_at=NOW)]


@pytest.fixture
def activity_service(client: TestClient):
    service = FakeActivityService()
    app.dependency_overrides[get_activity_service] = lambda: service
    yield service


def test_activity_post_is_stable_accepted_and_type_distinct(client: TestClient, activity_service: FakeActivityService) -> None:
    payload = {"elderly_id": "E001", "value": "active", "recorded_at": NOW.isoformat()}
    first = client.post("/api/activity", json=payload, headers={"Idempotency-Key": "same-key"})
    second = client.post("/api/activity", json=payload, headers={"Idempotency-Key": "same-key"})
    assert first.status_code == second.status_code == 202
    assert first.json()["data"]["event_id"] == second.json()["data"]["event_id"]
    assert UUID(first.json()["data"]["event_id"]) != derive_health_event_id(
        "E001", "same-key"
    )


@pytest.mark.parametrize("error,status", [(ElderlyProfileNotFound("E404"), 404), (IdempotencyConflict("Idempotency-Key was already used with a different payload"), 409), (ActivityBrokerUnavailable(), 503), (ActivityStorageUnavailable(), 503)])
def test_activity_post_maps_failures(client: TestClient, activity_service: FakeActivityService, error: Exception, status: int) -> None:
    activity_service.error = error
    response = client.post(
        "/api/activity",
        json={"elderly_id": "E001", "value": "inactive", "recorded_at": NOW.isoformat()},
        headers={"Idempotency-Key": "event-1"},
    )
    assert response.status_code == status
    assert set(response.json()) == {"success", "message", "data"}
    assert response.json()["success"] is False


def test_activity_validation_uses_failure_envelope(client: TestClient, activity_service: FakeActivityService) -> None:
    response = client.post(
        "/api/activity",
        json={"elderly_id": "E001", "value": "moving"},
        headers={"Idempotency-Key": "event-1"},
    )

    assert response.status_code == 422
    assert set(response.json()) == {"success", "message", "data"}
    assert response.json()["success"] is False


def test_activity_history_is_bounded(client: TestClient, activity_service: FakeActivityService) -> None:
    response = client.get("/api/activity/E001?limit=10&offset=2")
    assert response.status_code == 200
    assert response.json()["data"][0]["value"] == "active"
    assert activity_service.calls == [("E001", 10, 2)]
    assert client.get("/api/activity/E001?limit=101").status_code == 422

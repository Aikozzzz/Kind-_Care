from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_device_service
from app.main import app
from app.models.device import DeviceEvent, DeviceEventRecord, DeviceHeartbeatCreate
from app.services.device import DeviceBrokerUnavailable, DeviceStorageUnavailable
from app.services.elderly import ElderlyProfileNotFound
from app.services.idempotency import IdempotencyConflict


NOW = datetime(2026, 7, 17, 8, 0, tzinfo=UTC)


class FakeDeviceService:
    def __init__(self) -> None:
        self.error: Exception | None = None
        self.calls: list[object] = []

    async def queue_event(self, request: DeviceHeartbeatCreate, event_id: UUID, idempotency_key: str) -> DeviceEvent:
        if self.error:
            raise self.error
        self.calls.append((request, event_id, idempotency_key))
        return DeviceEvent(event_id=event_id, received_at=NOW, **request.model_dump())

    async def list_events(self, elderly_id: str, limit: int, offset: int) -> list[DeviceEventRecord]:
        self.calls.append((elderly_id, limit, offset))
        event = DeviceEvent(event_id=UUID(int=2), elderly_id=elderly_id, recorded_at=NOW, received_at=NOW)
        return [DeviceEventRecord(**event.model_dump(), created_at=NOW)]


@pytest.fixture
def device_service(client: TestClient):
    service = FakeDeviceService()
    app.dependency_overrides[get_device_service] = lambda: service
    yield service


def test_heartbeat_post_is_stable_and_accepted(client: TestClient, device_service: FakeDeviceService) -> None:
    payload = {"elderly_id": "E001", "recorded_at": NOW.isoformat()}
    first = client.post("/api/device-status", json=payload, headers={"Idempotency-Key": "heartbeat-1"})
    second = client.post("/api/device-status", json=payload, headers={"Idempotency-Key": "heartbeat-1"})
    assert first.status_code == second.status_code == 202
    assert first.json()["data"]["event_id"] == second.json()["data"]["event_id"]


@pytest.mark.parametrize("error,status", [(ElderlyProfileNotFound("E404"), 404), (IdempotencyConflict("Idempotency-Key was already used with a different payload"), 409), (DeviceBrokerUnavailable(), 503), (DeviceStorageUnavailable(), 503)])
def test_heartbeat_post_maps_failures(client: TestClient, device_service: FakeDeviceService, error: Exception, status: int) -> None:
    device_service.error = error
    response = client.post(
        "/api/device-status",
        json={"elderly_id": "E001", "recorded_at": NOW.isoformat()},
        headers={"Idempotency-Key": "heartbeat-1"},
    )
    assert response.status_code == status
    assert set(response.json()) == {"success", "message", "data"}
    assert response.json()["success"] is False


def test_device_validation_uses_failure_envelope(client: TestClient, device_service: FakeDeviceService) -> None:
    response = client.post(
        "/api/device-status",
        json={"elderly_id": "E001"},
        headers={"Idempotency-Key": "heartbeat-1"},
    )

    assert response.status_code == 422
    assert set(response.json()) == {"success", "message", "data"}
    assert response.json()["success"] is False


def test_device_history_is_bounded(client: TestClient, device_service: FakeDeviceService) -> None:
    response = client.get("/api/device-status/E001?limit=8&offset=1")
    assert response.status_code == 200
    assert device_service.calls == [("E001", 8, 1)]
    assert client.get("/api/device-status/E001?offset=10001").status_code == 422

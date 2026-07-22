from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_alert_service
from app.main import app
from app.models.health import AlertRecord
from app.services.alerts import AlertConflict, AlertNotFound, AlertStorageUnavailable


NOW = datetime(2026, 7, 18, 8, 0, tzinfo=UTC)


class FakeAlertService:
    def __init__(self) -> None:
        self.calls: list[object] = []
        self.error: Exception | None = None

    async def list(self, elderly_id, limit, offset, severity, alert_status):
        self.calls.append((elderly_id, limit, offset, severity, alert_status))
        return [alert_record()]

    async def update_status(self, alert_id, target_status):
        if self.error:
            raise self.error
        self.calls.append((alert_id, target_status))
        return alert_record(status=target_status)


def alert_record(status="unresolved"):
    values = {
        "alert_id": "a74cfda8-d0ef-518e-a671-a2eabca7f6b0",
        "event_id": "008b2d23-93e6-5ef5-b676-f629c63c8bbb",
        "elderly_id": "E001",
        "alert_type": "missed_reminder",
        "severity": "warning",
        "status": status,
        "message": "Medicine reminder was missed",
        "created_at": NOW,
    }
    if status == "acknowledged":
        values["acknowledged_at"] = NOW
    if status == "resolved":
        values["resolved_at"] = NOW
    return AlertRecord(**values)


@pytest.fixture
def alert_service(client: TestClient):
    service = FakeAlertService()
    app.dependency_overrides[get_alert_service] = lambda: service
    yield service


def test_alert_get_uses_bounded_validated_filters(client, alert_service):
    response = client.get("/api/alerts/E001?limit=5&offset=1&severity=warning&status=acknowledged")
    assert response.status_code == 200
    assert alert_service.calls == [("E001", 5, 1, "warning", "acknowledged")]
    assert response.json()["data"][0]["alert_id"] == "a74cfda8-d0ef-518e-a671-a2eabca7f6b0"
    assert client.get("/api/alerts/E001?severity=critical").status_code == 422


@pytest.mark.parametrize("target", ["acknowledged", "resolved"])
def test_alert_patch_supports_lifecycle_targets(client, alert_service, target):
    alert_id = "a74cfda8-d0ef-518e-a671-a2eabca7f6b0"
    response = client.patch(f"/api/alerts/{alert_id}", json={"status": target})
    assert response.status_code == 200
    assert response.json()["data"]["status"] == target
    assert alert_service.calls == [(alert_id, target)]


@pytest.mark.parametrize(
    "error,status",
    [(AlertNotFound("missing"), 404), (AlertConflict("invalid"), 409), (AlertStorageUnavailable(), 503)],
)
def test_alert_patch_maps_failures(client, alert_service, error, status):
    alert_service.error = error
    response = client.patch(
        "/api/alerts/a74cfda8-d0ef-518e-a671-a2eabca7f6b0",
        json={"status": "resolved"},
    )
    assert response.status_code == status
    assert response.json()["success"] is False

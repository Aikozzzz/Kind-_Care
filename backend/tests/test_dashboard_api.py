from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_dashboard_service
from app.main import app
from app.models.dashboard import DashboardSummary
from app.models.elderly import ElderlyProfile
from app.models.health import AlertRecord
from app.services.dashboard import DashboardStorageUnavailable
from app.services.elderly import ElderlyProfileNotFound


def summary() -> DashboardSummary:
    now = datetime(2026, 7, 16, 10, 30, tzinfo=UTC)
    return DashboardSummary(
        profile=ElderlyProfile(
            elderly_id="E001",
            full_name="Margaret Lee",
            date_of_birth=date(1948, 4, 12),
            active=True,
            created_at=now,
            updated_at=now,
        ),
        latest_health=None,
        current_risk="normal",
        current_alert=AlertRecord(
            alert_id="a74cfda8-d0ef-518e-a671-a2eabca7f6b0",
            event_id="008b2d23-93e6-5ef5-b676-f629c63c8bbb",
            elderly_id="E001",
            alert_type="device_offline",
            severity="warning",
            status="acknowledged",
            message="Monitoring device is offline",
            created_at=now,
        ),
        recent_alerts=[],
    )


class FakeDashboardService:
    def __init__(self) -> None:
        self.error: Exception | None = None

    async def get_summary(self, elderly_id: str) -> DashboardSummary:
        if self.error:
            raise self.error
        return summary()


@pytest.fixture
def dashboard_service(client: TestClient) -> FakeDashboardService:
    service = FakeDashboardService()
    app.dependency_overrides[get_dashboard_service] = lambda: service
    yield service


def test_dashboard_route_returns_typed_summary(
    client: TestClient, dashboard_service: FakeDashboardService
) -> None:
    response = client.get("/api/dashboard/E001")

    assert response.status_code == 200
    assert response.json()["data"]["profile"]["full_name"] == "Margaret Lee"
    assert response.json()["data"]["current_risk"] == "normal"
    assert response.json()["data"]["current_alert"]["alert_type"] == "device_offline"
    assert response.json()["data"]["latest_activity"] is None
    assert response.json()["data"]["device_status"] is None


def test_dashboard_summary_model_exposes_nullable_current_alert() -> None:
    value = summary().model_dump(mode="json")

    assert value["current_alert"]["status"] == "acknowledged"
    assert summary().model_copy(update={"current_alert": None}).model_dump()[
        "current_alert"
    ] is None


def test_dashboard_route_returns_clean_not_found(
    client: TestClient, dashboard_service: FakeDashboardService
) -> None:
    dashboard_service.error = ElderlyProfileNotFound("E404")

    response = client.get("/api/dashboard/E404")

    assert response.status_code == 404
    assert response.json() == {"detail": "Elderly profile E404 not found"}


def test_dashboard_route_returns_clean_service_unavailable(
    client: TestClient, dashboard_service: FakeDashboardService
) -> None:
    dashboard_service.error = DashboardStorageUnavailable()

    response = client.get("/api/dashboard/E001")

    assert response.status_code == 503
    assert response.json() == {
        "success": False,
        "message": "Dashboard data storage is unavailable",
        "data": {"status": "unavailable"},
    }

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_alert_service, get_health_service
from app.main import app
from app.models.health import AlertRecord, HealthEvent, HealthEventCreate, HealthRecord
from app.services.elderly import ElderlyProfileNotFound
from app.services.health import HealthBrokerUnavailable, HealthStorageUnavailable
from app.services.idempotency import IdempotencyConflict


def valid_payload(**updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "elderly_id": "E001",
        "heart_rate": 80,
        "temperature": 36.7,
        "oxygen_level": 97,
        "blood_pressure": "120/80",
        "movement_status": "active",
        "medicine_status": "taken",
        "emergency_pressed": False,
    }
    payload.update(updates)
    return payload


class FakeHealthService:
    def __init__(self) -> None:
        self.queued: list[HealthEvent] = []
        self.idempotency_keys: list[str] = []
        self.queue_error: Exception | None = None
        self.health_calls: list[tuple[str, int, int]] = []
        self.alert_calls: list[tuple[str, int, int, str | None, str | None]] = []
        now = datetime(2026, 7, 10, 10, 30, tzinfo=UTC)
        event = HealthEvent(
            **valid_payload(event_id=str(uuid4()), recorded_at=now.isoformat())
        )
        self.health_records = [
            HealthRecord(**event.model_dump(), risk_level="normal", created_at=now)
        ]
        self.alert_records = [
            AlertRecord(
                alert_id="a74cfda8-d0ef-518e-a671-a2eabca7f6b0",
                event_id=event.event_id,
                elderly_id="E001",
                alert_type="high_temperature",
                severity="warning",
                status="unresolved",
                message="High temperature detected",
                created_at=now,
            )
        ]

    async def queue_event(
        self,
        request: HealthEventCreate,
        event_id: UUID,
        idempotency_key: str,
    ) -> HealthEvent:
        if self.queue_error is not None:
            raise self.queue_error
        event = HealthEvent(event_id=event_id, **request.model_dump())
        self.queued.append(event)
        self.idempotency_keys.append(idempotency_key)
        return event

    async def list_health(
        self, elderly_id: str, limit: int, offset: int
    ) -> list[HealthRecord]:
        self.health_calls.append((elderly_id, limit, offset))
        return self.health_records

    async def list_alerts(
        self,
        elderly_id: str,
        limit: int,
        offset: int,
        severity: str | None,
        alert_status: str | None,
    ) -> list[AlertRecord]:
        self.alert_calls.append(
            (elderly_id, limit, offset, severity, alert_status)
        )
        return self.alert_records

    async def list(self, elderly_id, limit, offset, severity, alert_status):
        return await self.list_alerts(elderly_id, limit, offset, severity, alert_status)


@pytest.fixture
def health_service(client: TestClient) -> FakeHealthService:
    service = FakeHealthService()
    app.dependency_overrides[get_health_service] = lambda: service
    app.dependency_overrides[get_alert_service] = lambda: service
    yield service


def test_post_health_queues_event_and_returns_accepted_envelope(
    client: TestClient,
    health_service: FakeHealthService,
) -> None:
    response = client.post(
        "/api/health",
        json=valid_payload(),
        headers={"Idempotency-Key": "test-request"},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["success"] is True
    assert body["message"] == "Health event queued successfully"
    assert body["data"]["elderly_id"] == "E001"
    assert body["data"]["status"] == "queued"
    assert UUID(body["data"]["event_id"])
    assert health_service.queued[0].event_id == UUID(body["data"]["event_id"])


@pytest.mark.parametrize(
    "unknown_field",
    ["event_id", "heart_rate_bpm", "unexpected"],
)
def test_post_health_rejects_client_identity_and_unknown_fields(
    client: TestClient,
    health_service: FakeHealthService,
    unknown_field: str,
) -> None:
    response = client.post(
        "/api/health",
        json=valid_payload(**{unknown_field: str(uuid4())}),
        headers={"Idempotency-Key": "test-request"},
    )

    assert response.status_code == 422
    assert health_service.queued == []


def test_post_health_returns_not_found_for_missing_or_inactive_profile(
    client: TestClient,
    health_service: FakeHealthService,
) -> None:
    health_service.queue_error = ElderlyProfileNotFound("E404")

    response = client.post(
        "/api/health",
        json=valid_payload(elderly_id="E404"),
        headers={"Idempotency-Key": "test-request"},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Elderly profile E404 not found"}


def test_post_health_maps_broker_failure_to_service_unavailable(
    client: TestClient,
    health_service: FakeHealthService,
) -> None:
    health_service.queue_error = HealthBrokerUnavailable()

    response = client.post(
        "/api/health",
        json=valid_payload(),
        headers={"Idempotency-Key": "test-request"},
    )

    assert response.status_code == 503
    assert response.json() == {
        "success": False,
        "message": "Health event broker is unavailable",
        "data": {"status": "unavailable"},
    }


def test_post_health_maps_payload_conflict_to_client_visible_409(
    client: TestClient, health_service: FakeHealthService
) -> None:
    health_service.queue_error = IdempotencyConflict(
        "Idempotency-Key was already used with a different payload"
    )

    response = client.post(
        "/api/health",
        json=valid_payload(),
        headers={"Idempotency-Key": "conflicting-key"},
    )

    assert response.status_code == 409
    assert response.json() == {
        "success": False,
        "message": "Idempotency-Key was already used with a different payload",
        "data": {"status": "conflict"},
    }


def test_post_health_requires_idempotency_key(
    client: TestClient,
    health_service: FakeHealthService,
) -> None:
    response = client.post("/api/health", json=valid_payload())

    assert response.status_code == 422
    assert health_service.queued == []


@pytest.mark.parametrize("key", ["", "contains space", "x" * 129])
def test_post_health_rejects_invalid_idempotency_key(
    client: TestClient,
    health_service: FakeHealthService,
    key: str,
) -> None:
    response = client.post(
        "/api/health",
        json=valid_payload(),
        headers={"Idempotency-Key": key},
    )

    assert response.status_code == 422
    assert health_service.queued == []


def test_post_health_derives_stable_event_id_from_profile_and_key(
    client: TestClient,
    health_service: FakeHealthService,
) -> None:
    headers = {"Idempotency-Key": "sensor-retry-1"}

    first = client.post("/api/health", json=valid_payload(), headers=headers)
    second = client.post(
        "/api/health",
        json=valid_payload(heart_rate=81),
        headers=headers,
    )

    expected = UUID("8e971b76-cef5-544b-9973-26a86041a7ac")
    assert first.status_code == second.status_code == 202
    assert UUID(first.json()["data"]["event_id"]) == expected
    assert UUID(second.json()["data"]["event_id"]) == expected
    assert [event.event_id for event in health_service.queued] == [expected, expected]
    assert [event.heart_rate for event in health_service.queued] == [80, 81]


def test_health_history_returns_latest_records_with_pagination(
    client: TestClient,
    health_service: FakeHealthService,
) -> None:
    response = client.get("/api/health/E001?limit=10&offset=2")

    assert response.status_code == 200
    assert response.json()["data"][0]["risk_level"] == "normal"
    assert health_service.health_calls == [("E001", 10, 2)]


def test_alert_history_applies_severity_and_status_filters(
    client: TestClient,
    health_service: FakeHealthService,
) -> None:
    response = client.get(
        "/api/alerts/E001?limit=5&offset=1&severity=warning&status=unresolved"
    )

    assert response.status_code == 200
    assert response.json()["data"][0]["alert_type"] == "high_temperature"
    assert health_service.alert_calls == [
        ("E001", 5, 1, "warning", "unresolved")
    ]


@pytest.mark.parametrize(
    ("path", "query"),
    [
        ("/api/health/E001", "limit=0"),
        ("/api/health/E001", "limit=101"),
        ("/api/health/E001", "offset=-1"),
        ("/api/health/E001", "offset=10001"),
        ("/api/alerts/E001", "severity=normal"),
        ("/api/alerts/E001", "status=closed"),
    ],
)
def test_history_rejects_invalid_query_values(
    client: TestClient,
    health_service: FakeHealthService,
    path: str,
    query: str,
) -> None:
    assert client.get(f"{path}?{query}").status_code == 422


def test_history_maps_storage_failure_to_service_unavailable(
    client: TestClient,
    health_service: FakeHealthService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unavailable(*args: object, **kwargs: object) -> list[HealthRecord]:
        raise HealthStorageUnavailable()

    monkeypatch.setattr(health_service, "list_health", unavailable)

    response = client.get("/api/health/E001")

    assert response.status_code == 503
    assert response.json() == {
        "success": False,
        "message": "Health data storage is unavailable",
        "data": {"status": "unavailable"},
    }

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from app.models.health import (
    AlertRecord,
    HealthEvent,
    HealthEventCreate,
    HealthRecord,
    IdempotencyKey,
    QueuedHealthEvent,
)


def valid_payload(**updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "elderly_id": "E001",
        "heart_rate": 80,
        "temperature": 36.7,
        "oxygen_level": 97,
        "movement_status": "active",
        "medicine_status": "taken",
        "emergency_pressed": False,
    }
    payload.update(updates)
    return payload


def test_health_event_generates_uuid_and_utc_recorded_at() -> None:
    event = HealthEvent(**valid_payload())

    assert UUID(str(event.event_id)) == event.event_id
    assert event.recorded_at.tzinfo is UTC
    serialized = event.model_dump(mode="json")
    assert serialized["event_id"] == str(event.event_id)
    assert serialized["recorded_at"].endswith("Z")


@pytest.mark.parametrize("unknown_field", ["event_id", "heart_rate_bpm", "notes"])
def test_health_request_rejects_client_identity_and_unknown_fields(
    unknown_field: str,
) -> None:
    with pytest.raises(ValidationError):
        HealthEventCreate(**valid_payload(**{unknown_field: str(uuid4())}))


@pytest.mark.parametrize(
    "value",
    ["", "contains space", "contains\ttab", "caf\N{LATIN SMALL LETTER E WITH ACUTE}", "x" * 129],
)
def test_idempotency_key_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(IdempotencyKey).validate_python(value)


@pytest.mark.parametrize("value", ["a", "sensor.retry:2026/07/16", "~" * 128])
def test_idempotency_key_accepts_visible_ascii(value: str) -> None:
    assert TypeAdapter(IdempotencyKey).validate_python(value) == value


def test_internal_health_event_preserves_identity_timestamp_and_blood_pressure() -> None:
    event_id = uuid4()
    recorded_at = datetime(2026, 7, 10, 10, 30, tzinfo=UTC)

    event = HealthEvent(
        **valid_payload(
            event_id=str(event_id),
            recorded_at=recorded_at.isoformat(),
            blood_pressure="120/80",
        )
    )

    assert event.event_id == event_id
    assert event.recorded_at == recorded_at
    assert event.blood_pressure == "120/80"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("heart_rate", 19),
        ("heart_rate", 251),
        ("temperature", 24.9),
        ("temperature", 45.1),
        ("oxygen_level", 49),
        ("oxygen_level", 101),
    ],
)
def test_health_event_rejects_unrealistic_sensor_values(
    field: str,
    value: int | float,
) -> None:
    with pytest.raises(ValidationError):
        HealthEvent(**valid_payload(**{field: value}))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("heart_rate", 20),
        ("heart_rate", 250),
        ("temperature", 25),
        ("temperature", 45),
        ("oxygen_level", 50),
        ("oxygen_level", 100),
    ],
)
def test_health_event_accepts_sensor_range_endpoints(
    field: str,
    value: int | float,
) -> None:
    event = HealthEvent(**valid_payload(**{field: value}))
    assert getattr(event, field) == value


@pytest.mark.parametrize(
    "blood_pressure",
    ["120-80", "59/40", "251/80", "120/29", "120/151", "80/120"],
)
def test_health_event_rejects_invalid_blood_pressure(blood_pressure: str) -> None:
    with pytest.raises(ValidationError):
        HealthEvent(**valid_payload(blood_pressure=blood_pressure))


@pytest.mark.parametrize(
    ("field", "value"),
    [("movement_status", "sleeping"), ("medicine_status", "unknown")],
)
def test_health_event_rejects_unknown_statuses(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        HealthEvent(**valid_payload(**{field: value}))


def test_health_response_models_accept_worker_documents() -> None:
    now = datetime.now(UTC)
    event = HealthEvent(**valid_payload())

    queued = QueuedHealthEvent(
        event_id=event.event_id,
        elderly_id=event.elderly_id,
        status="queued",
    )
    record = HealthRecord(
        **event.model_dump(),
        risk_level="warning",
        created_at=now,
    )
    alert = AlertRecord(
        alert_id="a74cfda8-d0ef-518e-a671-a2eabca7f6b0",
        event_id=event.event_id,
        elderly_id=event.elderly_id,
        alert_type="high_temperature",
        severity="warning",
        status="unresolved",
        message="High temperature detected",
        created_at=now,
    )

    assert queued.status == "queued"
    assert record.risk_level == "warning"
    assert alert.status == "unresolved"
    assert isinstance(alert.alert_id, str)
    assert AlertRecord.model_json_schema()["properties"]["alert_id"]["type"] == "string"


def test_alert_record_requires_persisted_canonical_uuid_string() -> None:
    now = datetime.now(UTC)
    event = HealthEvent(**valid_payload())
    values = {
        "event_id": event.event_id,
        "elderly_id": event.elderly_id,
        "alert_type": "high_temperature",
        "severity": "warning",
        "status": "unresolved",
        "message": "High temperature detected",
        "created_at": now,
    }
    with pytest.raises(ValidationError):
        AlertRecord(**values)
    with pytest.raises(ValidationError):
        AlertRecord(alert_id="not-a-uuid", **values)

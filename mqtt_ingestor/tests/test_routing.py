import json

import pytest

from mqtt_ingestor.routing import (
    PermanentMessageError,
    TopicRoute,
    parse_topic,
    prepare_request,
)


@pytest.mark.parametrize("kind", ["health", "activity", "device", "reminder"])
def test_parse_topic_accepts_exact_contract(kind: str) -> None:
    assert parse_topic(f"kindcare/E_01-2/{kind}") == TopicRoute("E_01-2", kind)


@pytest.mark.parametrize(
    "topic",
    [
        "",
        "kindcare/E001",
        "kindcare/E001/health/extra",
        "other/E001/health",
        "kindcare/E001/unknown",
        "kindcare/E.001/health",
        "kindcare//health",
        f"kindcare/{'E' * 51}/health",
        "kindcare/é/health",
    ],
)
def test_parse_topic_rejects_every_noncontract_shape(topic: str) -> None:
    with pytest.raises(PermanentMessageError, match="topic"):
        parse_topic(topic)


def health_payload(**updates: object) -> bytes:
    payload: dict[str, object] = {
        "idempotency_key": "mqtt-E001-health-1",
        "elderly_id": "E001",
        "heart_rate": 80,
        "temperature": 36.7,
        "oxygen_level": 97,
        "movement_status": "active",
        "medicine_status": "taken",
        "emergency_pressed": False,
        "recorded_at": "2026-07-18T10:30:00+08:00",
    }
    payload.update(updates)
    return json.dumps(payload).encode()


def test_prepare_health_removes_only_key_and_preserves_timestamp_for_http_validation() -> None:
    request = prepare_request(
        TopicRoute("E001", "health"), health_payload(unexpected="reject-me"), 4096
    )

    assert request.method == "POST"
    assert request.path == "/api/health"
    assert request.idempotency_key == "mqtt-E001-health-1"
    assert "idempotency_key" not in request.body
    assert request.body["elderly_id"] == "E001"
    assert request.body["recorded_at"] == "2026-07-18T10:30:00+08:00"
    assert request.body["unexpected"] == "reject-me"


@pytest.mark.parametrize(
    ("kind", "payload", "path"),
    [
        (
            "activity",
            {
                "idempotency_key": "activity-1",
                "elderly_id": "E001",
                "value": "active",
                "recorded_at": "2026-07-18T02:30:00Z",
            },
            "/api/activity",
        ),
        (
            "device",
            {
                "idempotency_key": "device-1",
                "elderly_id": "E001",
                "recorded_at": "2026-07-18T02:30:00Z",
            },
            "/api/device-status",
        ),
    ],
)
def test_prepare_telemetry_routes_existing_http_body(kind, payload, path) -> None:
    request = prepare_request(
        TopicRoute("E001", kind), json.dumps(payload).encode(), 4096
    )

    assert request.method == "POST"
    assert request.path == path
    assert request.idempotency_key == payload["idempotency_key"]
    assert request.body == {key: value for key, value in payload.items() if key != "idempotency_key"}


def test_prepare_reminder_routes_only_taken_transition() -> None:
    reminder_id = "d90f15bc-cb99-49fa-8dcd-4cf1f664bb7f"
    payload = json.dumps(
        {
            "idempotency_key": "reminder-1",
            "elderly_id": "E001",
            "reminder_id": reminder_id,
            "status": "taken",
        }
    ).encode()

    request = prepare_request(TopicRoute("E001", "reminder"), payload, 4096)

    assert request.method == "PATCH"
    assert request.path == f"/api/reminders/{reminder_id}"
    assert request.body == {"elderly_id": "E001", "status": "taken"}
    assert request.idempotency_key == "reminder-1"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"\xff", "UTF-8"),
        (b"{", "JSON"),
        (b"[]", "object"),
        (json.dumps({"elderly_id": "E001"}).encode(), "idempotency_key"),
        (
            json.dumps({"idempotency_key": "has space", "elderly_id": "E001"}).encode(),
            "idempotency_key",
        ),
        (
            json.dumps({"idempotency_key": "x" * 129, "elderly_id": "E001"}).encode(),
            "idempotency_key",
        ),
        (
            json.dumps({"idempotency_key": "key", "elderly_id": "E002"}).encode(),
            "elderly_id",
        ),
    ],
)
def test_prepare_rejects_invalid_envelopes(payload: bytes, message: str) -> None:
    with pytest.raises(PermanentMessageError, match=message):
        prepare_request(TopicRoute("E001", "health"), payload, 4096)


def test_prepare_checks_size_before_decoding() -> None:
    with pytest.raises(PermanentMessageError, match="size"):
        prepare_request(TopicRoute("E001", "health"), b"\xff\xff", 1)


@pytest.mark.parametrize(
    "updates",
    [
        {"status": "missed"},
        {"reminder_id": "not-a-uuid"},
        {"extra": "field"},
    ],
)
def test_prepare_rejects_invalid_reminder_contract(updates: dict[str, object]) -> None:
    payload: dict[str, object] = {
        "idempotency_key": "reminder-1",
        "elderly_id": "E001",
        "reminder_id": "d90f15bc-cb99-49fa-8dcd-4cf1f664bb7f",
        "status": "taken",
    }
    payload.update(updates)

    with pytest.raises(PermanentMessageError, match="reminder"):
        prepare_request(TopicRoute("E001", "reminder"), json.dumps(payload).encode(), 4096)

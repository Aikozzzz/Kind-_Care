import io
import json
from http.client import IncompleteRead
from urllib.error import HTTPError, URLError

import pytest

from client_nodes.elderly_node import ElderlyNodeClient, NodeHTTPError


class Response:
    def __init__(self, payload: dict[str, object], status: int = 202) -> None:
        self.payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def reading() -> dict[str, object]:
    return {
        "elderly_id": "E001",
        "heart_rate": 82,
        "temperature": 36.8,
        "oxygen_level": 97,
        "blood_pressure": "121/79",
        "movement_status": "active",
        "medicine_status": "taken",
        "emergency_pressed": False,
    }


def test_transport_retry_reuses_one_idempotency_key_and_applies_backoff() -> None:
    requests = []
    timeouts = []
    sleeps = []

    def opener(request: object, timeout: float):
        requests.append(request)
        timeouts.append(timeout)
        if len(requests) == 1:
            raise URLError("temporary network failure")
        return Response(
            {
                "success": True,
                "data": {"event_id": "event-1", "status": "queued"},
            }
        )

    client = ElderlyNodeClient(
        "http://backend:8000",
        timeout=3.5,
        max_retries=2,
        backoff=0.25,
        opener=opener,
        sleep=sleeps.append,
        key_factory=lambda: "stable-event-key",
    )

    result = client.send_health(reading())

    assert result.event_id == "event-1"
    assert result.idempotency_key == "stable-event-key"
    assert [request.headers["Idempotency-key"] for request in requests] == [
        "stable-event-key",
        "stable-event-key",
    ]
    assert timeouts == [3.5, 3.5]
    assert sleeps == [0.25]


def test_http_error_is_reported_without_transport_retry() -> None:
    attempts = 0

    def opener(request: object, timeout: float):
        nonlocal attempts
        attempts += 1
        body = io.BytesIO(b'{"detail":"Elderly profile E404 not found"}')
        raise HTTPError(request.full_url, 404, "Not Found", {}, body)

    client = ElderlyNodeClient(
        "http://backend:8000",
        max_retries=3,
        opener=opener,
        sleep=lambda seconds: None,
    )

    with pytest.raises(NodeHTTPError, match="Elderly profile E404 not found") as error:
        client.send_health(reading(), idempotency_key="explicit-key")

    assert error.value.status_code == 404
    assert attempts == 1


def test_exhausted_transport_retries_raise_clear_error() -> None:
    attempts = 0

    def opener(request: object, timeout: float):
        nonlocal attempts
        attempts += 1
        raise TimeoutError("timed out")

    client = ElderlyNodeClient(
        "http://backend:8000",
        max_retries=1,
        opener=opener,
        sleep=lambda seconds: None,
    )

    with pytest.raises(ConnectionError, match="after 2 attempts"):
        client.send_health(reading())

    assert attempts == 2


def test_incomplete_response_retries_with_same_idempotency_key() -> None:
    requests = []

    class TruncatedResponse(Response):
        def read(self) -> bytes:
            raise IncompleteRead(b'{"success":', 30)

    def opener(request: object, timeout: float):
        requests.append(request)
        if len(requests) == 1:
            return TruncatedResponse({})
        return Response(
            {"success": True, "data": {"event_id": "event-2", "status": "queued"}}
        )

    client = ElderlyNodeClient(
        "http://backend:8000",
        max_retries=1,
        opener=opener,
        sleep=lambda seconds: None,
        key_factory=lambda: "truncated-response-key",
    )

    result = client.send_health(reading())

    assert result.event_id == "event-2"
    assert [request.headers["Idempotency-key"] for request in requests] == [
        "truncated-response-key",
        "truncated-response-key",
    ]


def test_activity_and_heartbeat_use_distinct_endpoints_and_keys() -> None:
    requests = []
    keys = iter(["activity-key", "heartbeat-key"])

    def opener(request: object, timeout: float):
        requests.append(request)
        return Response(
            {"success": True, "data": {"event_id": f"event-{len(requests)}", "status": "queued"}}
        )

    client = ElderlyNodeClient(
        "http://backend:8000", opener=opener, key_factory=lambda: next(keys)
    )
    activity = client.send_activity(
        {"elderly_id": "E001", "value": "inactive", "recorded_at": "2026-07-17T08:00:00Z"}
    )
    heartbeat = client.send_heartbeat(
        {"elderly_id": "E001", "recorded_at": "2026-07-17T08:00:00Z"}
    )

    assert [request.full_url for request in requests] == [
        "http://backend:8000/api/activity",
        "http://backend:8000/api/device-status",
    ]
    assert activity.idempotency_key == "activity-key"
    assert heartbeat.idempotency_key == "heartbeat-key"


def test_reminder_client_creates_with_stable_key_and_marks_taken() -> None:
    requests = []

    def opener(request: object, timeout: float):
        requests.append(request)
        if request.method == "POST":
            return Response(
                {
                    "success": True,
                    "data": {"reminder_id": "reminder-1", "status": "pending"},
                },
                status=201,
            )
        return Response(
            {
                "success": True,
                "data": {"reminder_id": "reminder-1", "status": "taken"},
            },
            status=200,
        )

    client = ElderlyNodeClient(
        "http://backend:8000", opener=opener, key_factory=lambda: "reminder-key"
    )
    created = client.create_reminder(
        {
            "elderly_id": "E001",
            "medicine_name": "Aspirin",
            "scheduled_for": "2026-07-18T08:00:00Z",
        }
    )
    taken = client.mark_reminder_taken(created.reminder_id, "E001")

    assert created.idempotency_key == "reminder-key"
    assert taken.status == "taken"
    assert [request.method for request in requests] == ["POST", "PATCH"]
    assert requests[0].headers["Idempotency-key"] == "reminder-key"
    assert requests[1].full_url.endswith("/api/reminders/reminder-1")
    assert json.loads(requests[1].data) == {"elderly_id": "E001", "status": "taken"}


def test_reminder_client_lists_bounded_history_for_profile() -> None:
    requests = []

    def opener(request: object, timeout: float):
        requests.append(request)
        return Response(
            {
                "success": True,
                "data": [
                    {"reminder_id": "reminder-1", "status": "pending"},
                    {"reminder_id": "reminder-2", "status": "missed"},
                ],
            },
            status=200,
        )

    client = ElderlyNodeClient("http://backend:8000", opener=opener)
    reminders = client.list_reminders("E 001", limit=20)

    assert [reminder.status for reminder in reminders] == ["pending", "missed"]
    assert requests[0].method == "GET"
    assert requests[0].full_url == "http://backend:8000/api/reminders/E%20001?limit=20"

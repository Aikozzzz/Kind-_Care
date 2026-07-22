import json
from io import BytesIO
from urllib.error import HTTPError, URLError

import pytest

from mqtt_ingestor.http_bridge import (
    HTTPBridge,
    HTTPResult,
    Outcome,
    classify_status,
    forward_with_retry,
)
from mqtt_ingestor.routing import PreparedRequest


class Response:
    def __init__(self, status: int = 202) -> None:
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None

    def read(self) -> bytes:
        return b'{}'


class RecordingOpener:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.requests = []
        self.timeouts = []

    def __call__(self, request, *, timeout: float):
        self.requests.append(request)
        self.timeouts.append(timeout)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class StopAfterWaits:
    def __init__(self, stop_after: int | None = None) -> None:
        self.stop_after = stop_after
        self.waits: list[float] = []
        self.stopped = False

    def is_set(self) -> bool:
        return self.stopped

    def wait(self, delay: float) -> bool:
        self.waits.append(delay)
        if self.stop_after is not None and len(self.waits) >= self.stop_after:
            self.stopped = True
        return self.stopped


def prepared(method: str = "POST") -> PreparedRequest:
    return PreparedRequest(
        method=method,
        path=(
            "/api/health"
            if method == "POST"
            else "/api/reminders/d90f15bc-cb99-49fa-8dcd-4cf1f664bb7f"
        ),
        body=(
            {"elderly_id": "E001"}
            if method == "POST"
            else {"elderly_id": "E001", "status": "taken"}
        ),
        idempotency_key="stable-key-1",
    )


@pytest.mark.parametrize("status", [200, 201, 202, 204, 299])
def test_classify_2xx_as_success(status: int) -> None:
    assert classify_status(status) is Outcome.SUCCESS


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 422, 499])
def test_classify_permanent_4xx(status: int) -> None:
    assert classify_status(status) is Outcome.PERMANENT


@pytest.mark.parametrize("status", [408, 425, 429, 500, 503, 599])
def test_classify_retryable_http_status(status: int) -> None:
    assert classify_status(status) is Outcome.TRANSIENT


def test_bridge_preserves_body_key_method_and_timeout() -> None:
    opener = RecordingOpener([Response(202), Response(200)])
    bridge = HTTPBridge("http://backend:8000/", timeout=7.5, opener=opener)

    assert bridge.send(prepared()) == HTTPResult(Outcome.SUCCESS, 202)
    assert bridge.send(prepared("PATCH")) == HTTPResult(Outcome.SUCCESS, 200)

    assert [request.full_url for request in opener.requests] == [
        "http://backend:8000/api/health",
        "http://backend:8000/api/reminders/d90f15bc-cb99-49fa-8dcd-4cf1f664bb7f",
    ]
    assert [request.method for request in opener.requests] == ["POST", "PATCH"]
    assert [json.loads(request.data) for request in opener.requests] == [
        {"elderly_id": "E001"},
        {"elderly_id": "E001", "status": "taken"},
    ]
    assert all(request.headers["Idempotency-key"] == "stable-key-1" for request in opener.requests)
    assert all(request.headers["Content-type"] == "application/json" for request in opener.requests)
    assert opener.timeouts == [7.5, 7.5]


def test_bridge_classifies_http_error_without_reading_or_logging_payload() -> None:
    error = HTTPError(
        "http://backend:8000/api/health",
        422,
        "validation",
        {},
        BytesIO(b'{"medical":"sensitive"}'),
    )
    bridge = HTTPBridge("http://backend:8000", timeout=2, opener=RecordingOpener([error]))

    assert bridge.send(prepared()) == HTTPResult(Outcome.PERMANENT, 422)
    assert error.fp.read() == b'{"medical":"sensitive"}'


@pytest.mark.parametrize("error", [URLError("offline"), TimeoutError(), OSError("reset")])
def test_bridge_classifies_transport_errors_as_transient(error: BaseException) -> None:
    bridge = HTTPBridge("http://backend:8000", timeout=2, opener=RecordingOpener([error]))

    assert bridge.send(prepared()) == HTTPResult(Outcome.TRANSIENT)


def test_retry_preserves_request_and_caps_exponential_backoff() -> None:
    request = prepared()
    bridge = HTTPBridge(
        "http://backend:8000",
        timeout=2,
        opener=RecordingOpener(
            [
                URLError("offline"),
                Response(503),
                Response(429),
                Response(202),
            ]
        ),
    )
    stop = StopAfterWaits()

    result = forward_with_retry(request, bridge, stop, 0.5, 1.0)

    assert result.http_result == HTTPResult(Outcome.SUCCESS, 202)
    assert result.attempts == 4
    assert stop.waits == [0.5, 1.0, 1.0]
    assert all(
        sent.headers["Idempotency-key"] == request.idempotency_key
        for sent in bridge.opener.requests
    )
    assert [json.loads(sent.data) for sent in bridge.opener.requests] == [request.body] * 4


def test_permanent_result_is_not_retried() -> None:
    opener = RecordingOpener([Response(409)])
    result = forward_with_retry(
        prepared(), HTTPBridge("http://backend:8000", 2, opener), StopAfterWaits(), 1, 10
    )

    assert result.http_result == HTTPResult(Outcome.PERMANENT, 409)
    assert result.attempts == 1
    assert len(opener.requests) == 1


def test_shutdown_interrupts_retry_backoff() -> None:
    opener = RecordingOpener([Response(503), Response(202)])
    stop = StopAfterWaits(stop_after=1)

    result = forward_with_retry(
        prepared(), HTTPBridge("http://backend:8000", 2, opener), stop, 1, 10
    )

    assert result.http_result is None
    assert result.attempts == 1
    assert len(opener.requests) == 1
    assert stop.waits == [1]


def test_retry_does_not_log_sensitive_reminder_path_or_each_attempt(caplog) -> None:
    reminder_id = "d90f15bc-cb99-49fa-8dcd-4cf1f664bb7f"
    request = PreparedRequest(
        method="PATCH",
        path=f"/api/reminders/{reminder_id}",
        body={"elderly_id": "E001", "status": "taken"},
        idempotency_key="reminder-key",
    )
    bridge = HTTPBridge(
        "http://backend:8000",
        2,
        RecordingOpener([Response(503), Response(200)]),
    )

    result = forward_with_retry(request, bridge, StopAfterWaits(), 0.001, 0.001)

    assert result.attempts == 2
    assert reminder_id not in caplog.text
    assert "/api/reminders" not in caplog.text

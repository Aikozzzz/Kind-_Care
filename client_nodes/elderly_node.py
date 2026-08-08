import json
import time
from dataclasses import dataclass
from http.client import IncompleteRead
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import quote
from uuid import uuid4


class NodeHTTPError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}: {message}")


@dataclass(frozen=True)
class SubmissionResult:
    event_id: str
    status: str
    idempotency_key: str


@dataclass(frozen=True)
class ReminderResult:
    reminder_id: str
    status: str
    idempotency_key: str | None = None


class ElderlyNodeClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 5.0,
        max_retries: int = 2,
        backoff: float = 0.5,
        opener: Callable[..., object] = urlopen,
        sleep: Callable[[float], None] = time.sleep,
        key_factory: Callable[[], str] = lambda: uuid4().hex,
        auth_token: str = "",
    ) -> None:
        self.health_url = f"{base_url.rstrip('/')}/api/health"
        self.activity_url = f"{base_url.rstrip('/')}/api/activity"
        self.device_status_url = f"{base_url.rstrip('/')}/api/device-status"
        self.reminders_url = f"{base_url.rstrip('/')}/api/reminders"
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff = backoff
        self.opener = opener
        self.sleep = sleep
        self.key_factory = key_factory
        self.auth_token = auth_token

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = dict(extra or {})
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        return headers

    def send_health(
        self,
        payload: dict[str, object],
        idempotency_key: str | None = None,
    ) -> SubmissionResult:
        return self._send(self.health_url, "Health", payload, idempotency_key)

    def send_activity(
        self,
        payload: dict[str, object],
        idempotency_key: str | None = None,
    ) -> SubmissionResult:
        return self._send(self.activity_url, "Activity", payload, idempotency_key)

    def send_heartbeat(
        self,
        payload: dict[str, object],
        idempotency_key: str | None = None,
    ) -> SubmissionResult:
        return self._send(
            self.device_status_url, "Device heartbeat", payload, idempotency_key
        )

    def create_reminder(
        self,
        payload: dict[str, object],
        idempotency_key: str | None = None,
    ) -> ReminderResult:
        event_key = idempotency_key or self.key_factory()
        data = self._send_json(
            self.reminders_url,
            "POST",
            payload,
            "Reminder",
            self._headers({"Idempotency-Key": event_key}),
        )
        return ReminderResult(
            reminder_id=str(data["reminder_id"]),
            status=str(data["status"]),
            idempotency_key=event_key,
        )

    def mark_reminder_taken(self, reminder_id: str, elderly_id: str) -> ReminderResult:
        data = self._send_json(
            f"{self.reminders_url}/{quote(reminder_id, safe='')}",
            "PATCH",
            {"elderly_id": elderly_id, "status": "taken"},
            "Reminder taken",
        )
        return ReminderResult(
            reminder_id=str(data["reminder_id"]), status=str(data["status"])
        )

    def list_reminders(self, elderly_id: str, *, limit: int = 50) -> list[ReminderResult]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        url = f"{self.reminders_url}/{quote(elderly_id, safe='')}?limit={limit}"
        request = Request(url, headers=self._headers(), method="GET")
        for attempt in range(self.max_retries + 1):
            try:
                with self.opener(request, timeout=self.timeout) as response:
                    body = json.loads(response.read().decode("utf-8"))
                return [
                    ReminderResult(
                        reminder_id=str(record["reminder_id"]),
                        status=str(record["status"]),
                    )
                    for record in body["data"]
                ]
            except HTTPError as error:
                raise NodeHTTPError(error.code, _http_error_message(error)) from error
            except (IncompleteRead, TimeoutError, URLError, OSError) as error:
                if attempt == self.max_retries:
                    raise ConnectionError(
                        f"Reminder history failed after {attempt + 1} attempts: {error}"
                    ) from error
                self.sleep(self.backoff * (2**attempt))
        raise RuntimeError("unreachable")

    def _send_json(
        self,
        url: str,
        method: str,
        payload: dict[str, object],
        kind: str,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, object]:
        request = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers({"Content-Type": "application/json", **(extra_headers or {})}),
            method=method,
        )
        for attempt in range(self.max_retries + 1):
            try:
                with self.opener(request, timeout=self.timeout) as response:
                    body = json.loads(response.read().decode("utf-8"))
                return body["data"]
            except HTTPError as error:
                raise NodeHTTPError(error.code, _http_error_message(error)) from error
            except (IncompleteRead, TimeoutError, URLError, OSError) as error:
                if attempt == self.max_retries:
                    raise ConnectionError(
                        f"{kind} submission failed after {attempt + 1} attempts: {error}"
                    ) from error
                self.sleep(self.backoff * (2**attempt))
        raise RuntimeError("unreachable")

    def _send(
        self,
        url: str,
        kind: str,
        payload: dict[str, object],
        idempotency_key: str | None,
    ) -> SubmissionResult:
        event_key = idempotency_key or self.key_factory()
        request = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers({
                "Content-Type": "application/json",
                "Idempotency-Key": event_key,
            }),
            method="POST",
        )

        for attempt in range(self.max_retries + 1):
            try:
                with self.opener(request, timeout=self.timeout) as response:
                    body = json.loads(response.read().decode("utf-8"))
                data = body["data"]
                return SubmissionResult(
                    event_id=str(data["event_id"]),
                    status=str(data["status"]),
                    idempotency_key=event_key,
                )
            except HTTPError as error:
                raise NodeHTTPError(error.code, _http_error_message(error)) from error
            except (IncompleteRead, TimeoutError, URLError, OSError) as error:
                if attempt == self.max_retries:
                    raise ConnectionError(
                        f"{kind} submission failed after {attempt + 1} attempts: {error}"
                    ) from error
                self.sleep(self.backoff * (2**attempt))

        raise RuntimeError("unreachable")


def _http_error_message(error: HTTPError) -> str:
    try:
        payload = json.loads(error.read().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return error.reason
    return str(payload.get("detail") or payload.get("message") or error.reason)

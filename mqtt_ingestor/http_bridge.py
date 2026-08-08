import json
from dataclasses import dataclass
from enum import Enum
from http.client import IncompleteRead, RemoteDisconnected
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from mqtt_ingestor.routing import PreparedRequest


class Outcome(Enum):
    SUCCESS = "success"
    PERMANENT = "permanent"
    TRANSIENT = "transient"


@dataclass(frozen=True)
class HTTPResult:
    outcome: Outcome
    status_code: int | None = None


@dataclass(frozen=True)
class ForwardResult:
    http_result: HTTPResult | None
    attempts: int


def classify_status(status_code: int) -> Outcome:
    if 200 <= status_code < 300:
        return Outcome.SUCCESS
    if 400 <= status_code < 500 and status_code not in {408, 425, 429}:
        return Outcome.PERMANENT
    return Outcome.TRANSIENT


class HTTPBridge:
    def __init__(
        self,
        base_url: str,
        timeout: float,
        opener=urlopen,
        *,
        api_token: str = "",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.api_token = api_token
        self.opener = opener

    def send(self, prepared: PreparedRequest) -> HTTPResult:
        headers = {
            "Content-Type": "application/json",
            "Idempotency-Key": prepared.idempotency_key,
        }
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        request = Request(
            f"{self.base_url}{prepared.path}",
            data=json.dumps(prepared.body, separators=(",", ":")).encode("utf-8"),
            headers=headers,
            method=prepared.method,
        )
        try:
            with self.opener(request, timeout=self.timeout) as response:
                status_code = response.status
                response.read()
            return HTTPResult(classify_status(status_code), status_code)
        except HTTPError as error:
            return HTTPResult(classify_status(error.code), error.code)
        except (
            IncompleteRead,
            RemoteDisconnected,
            TimeoutError,
            URLError,
            OSError,
        ):
            return HTTPResult(Outcome.TRANSIENT)


def forward_with_retry(
    prepared: PreparedRequest,
    bridge: HTTPBridge,
    stop_event,
    initial_backoff: float,
    maximum_backoff: float,
) -> ForwardResult:
    delay = initial_backoff
    attempt = 1
    while not stop_event.is_set():
        result = bridge.send(prepared)
        if result.outcome is not Outcome.TRANSIENT:
            return ForwardResult(result, attempt)
        if stop_event.wait(delay):
            return ForwardResult(None, attempt)
        delay = min(delay * 2, maximum_backoff)
        attempt += 1
    return ForwardResult(None, max(attempt - 1, 0))

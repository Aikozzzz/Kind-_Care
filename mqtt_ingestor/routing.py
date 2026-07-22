import json
import re
from dataclasses import dataclass
from typing import Literal
from uuid import UUID


MessageKind = Literal["health", "activity", "device", "reminder"]
TOPIC_PATTERN = re.compile(
    r"kindcare/([A-Za-z0-9_-]{1,50})/(health|activity|device|reminder)"
)
IDEMPOTENCY_KEY_PATTERN = re.compile(r"[!-~]{1,128}")
TELEMETRY_PATHS = {
    "health": "/api/health",
    "activity": "/api/activity",
    "device": "/api/device-status",
}


class PermanentMessageError(ValueError):
    pass


@dataclass(frozen=True)
class TopicRoute:
    elderly_id: str
    kind: MessageKind


@dataclass(frozen=True)
class PreparedRequest:
    method: Literal["POST", "PATCH"]
    path: str
    body: dict[str, object]
    idempotency_key: str


def parse_topic(topic: str) -> TopicRoute:
    match = TOPIC_PATTERN.fullmatch(topic)
    if match is None:
        raise PermanentMessageError("invalid MQTT topic")
    elderly_id, kind = match.groups()
    return TopicRoute(elderly_id=elderly_id, kind=kind)  # type: ignore[arg-type]


def _decode_payload(payload: bytes, max_payload_bytes: int) -> dict[str, object]:
    if len(payload) > max_payload_bytes:
        raise PermanentMessageError("payload exceeds size limit")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PermanentMessageError("payload must be UTF-8") from error
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError as error:
        raise PermanentMessageError("payload must be valid JSON") from error
    if not isinstance(decoded, dict):
        raise PermanentMessageError("payload must be a JSON object")
    return decoded


def _idempotency_key(decoded: dict[str, object]) -> str:
    value = decoded.get("idempotency_key")
    if not isinstance(value, str) or IDEMPOTENCY_KEY_PATTERN.fullmatch(value) is None:
        raise PermanentMessageError("invalid or missing idempotency_key")
    return value


def prepare_request(
    route: TopicRoute,
    payload: bytes,
    max_payload_bytes: int,
) -> PreparedRequest:
    decoded = _decode_payload(payload, max_payload_bytes)
    idempotency_key = _idempotency_key(decoded)
    payload_elderly_id = decoded.get("elderly_id")
    if payload_elderly_id is not None and payload_elderly_id != route.elderly_id:
        raise PermanentMessageError("payload elderly_id does not match topic")

    if route.kind == "reminder":
        expected = {"idempotency_key", "elderly_id", "reminder_id", "status"}
        reminder_id = decoded.get("reminder_id")
        if set(decoded) != expected or decoded.get("status") != "taken":
            raise PermanentMessageError("invalid reminder payload")
        if not isinstance(reminder_id, str):
            raise PermanentMessageError("invalid reminder payload")
        try:
            if str(UUID(reminder_id)) != reminder_id:
                raise ValueError
        except ValueError as error:
            raise PermanentMessageError("invalid reminder payload") from error
        return PreparedRequest(
            method="PATCH",
            path=f"/api/reminders/{reminder_id}",
            body={"elderly_id": route.elderly_id, "status": "taken"},
            idempotency_key=idempotency_key,
        )

    body = {key: value for key, value in decoded.items() if key != "idempotency_key"}
    return PreparedRequest(
        method="POST",
        path=TELEMETRY_PATHS[route.kind],
        body=body,
        idempotency_key=idempotency_key,
    )

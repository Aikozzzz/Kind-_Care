import os
from dataclasses import dataclass


def _required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise ValueError(f"{name} is required")
    return value


def _positive_int(name: str, default: str) -> int:
    try:
        value = int(os.environ.get(name, default))
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _positive_float(name: str, default: str) -> float:
    try:
        value = float(os.environ.get(name, default))
    except ValueError as error:
        raise ValueError(f"{name} must be a number") from error
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


@dataclass(frozen=True)
class Settings:
    broker: str
    port: int
    username: str
    password: str
    client_id: str
    api_base_url: str
    max_payload_bytes: int
    queue_size: int
    http_timeout: float
    retry_initial_seconds: float
    retry_max_seconds: float
    reconnect_min_seconds: int
    reconnect_max_seconds: int
    health_file: str
    health_max_age_seconds: float

    @classmethod
    def from_env(cls) -> "Settings":
        port = _positive_int("MQTT_PORT", "1883")
        if port > 65_535:
            raise ValueError("MQTT_PORT must not exceed 65535")
        retry_initial = _positive_float("MQTT_RETRY_INITIAL_SECONDS", "0.5")
        retry_max = _positive_float("MQTT_RETRY_MAX_SECONDS", "30")
        reconnect_min = _positive_int("MQTT_RECONNECT_MIN_SECONDS", "1")
        reconnect_max = _positive_int("MQTT_RECONNECT_MAX_SECONDS", "30")
        if retry_max < retry_initial:
            raise ValueError("MQTT_RETRY_MAX_SECONDS must be at least the initial delay")
        if reconnect_max < reconnect_min:
            raise ValueError("MQTT_RECONNECT_MAX_SECONDS must be at least the minimum delay")
        return cls(
            broker=os.environ.get("MQTT_BROKER", "mosquitto"),
            port=port,
            username=_required("MQTT_USERNAME"),
            password=_required("MQTT_PASSWORD"),
            client_id=os.environ.get("MQTT_CLIENT_ID", "kindcare-mqtt-ingestor"),
            api_base_url=os.environ.get("API_BASE_URL", "http://backend:8000").rstrip("/"),
            max_payload_bytes=_positive_int("MQTT_MAX_PAYLOAD_BYTES", "16384"),
            queue_size=_positive_int("MQTT_QUEUE_SIZE", "100"),
            http_timeout=_positive_float("MQTT_HTTP_TIMEOUT", "10"),
            retry_initial_seconds=retry_initial,
            retry_max_seconds=retry_max,
            reconnect_min_seconds=reconnect_min,
            reconnect_max_seconds=reconnect_max,
            health_file=os.environ.get(
                "MQTT_HEALTH_FILE", "/tmp/kindcare-mqtt-connected"
            ),
            health_max_age_seconds=_positive_float(
                "MQTT_HEALTH_MAX_AGE_SECONDS", "15"
            ),
        )

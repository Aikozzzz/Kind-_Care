import os
import time
from pathlib import Path


def mark_connected(path: str) -> None:
    Path(path).touch()


def clear_connected(path: str) -> None:
    Path(path).unlink(missing_ok=True)


def is_healthy(path: str, max_age_seconds: float) -> bool:
    marker = Path(path)
    try:
        age = time.time() - marker.stat().st_mtime
    except FileNotFoundError:
        return False
    return 0 <= age <= max_age_seconds


def main() -> int:
    path = os.environ.get("MQTT_HEALTH_FILE", "/tmp/kindcare-mqtt-connected")
    max_age = float(os.environ.get("MQTT_HEALTH_MAX_AGE_SECONDS", "15"))
    return 0 if max_age > 0 and is_healthy(path, max_age) else 1


if __name__ == "__main__":
    raise SystemExit(main())
